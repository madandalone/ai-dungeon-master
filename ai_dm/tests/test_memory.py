import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from ai_dm.context import message_chars
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ContextBudgetError
from ai_dm.memory import history_record, select_history
from ai_dm.models import TurnHistoryEntry
from ai_dm.main import DEMO_RULES
from ai_dm.tests.test_dm_engine import TURN, completion, fake_client, turn_request
from ai_dm.tests.test_history import action, session
from engine.dice import Dice, SequenceSource
from integration import TurnOrchestrator
from rules_service import RulesService


def record(text, *, notes=""):
    return TurnHistoryEntry(player_action="Наблюдаю.", narrative=text, dm_notes=notes)


def request_with_history():
    request = turn_request()
    request.player_action = "Где серебряный ключ?"
    request.history = [
        record("Серебряный ключ хранится у кузнеца."),
        record("На площади выступает музыкант."),
        record("Кузнец ушёл.", notes="Серебряный ключ спрятан под верстаком."),
        *[record(f"Путники отдыхают. День {index}.") for index in range(10)],
    ]
    return request


def prompt_blocks(messages):
    content = messages[1]["content"]
    decoder = json.JSONDecoder()
    recent = decoder.raw_decode(content.split(
        "### ИСТОРИЯ ПРИНЯТЫХ ХОДОВ (от ранних к поздним):\n", 1)[1])[0]
    memories = decoder.raw_decode(content.split(
        "### РАНЕЕ ПРОИЗОШЕДШИЕ СОБЫТИЯ (выборка, от ранних к поздним):\n", 1)[1])[0]
    return recent, memories


class MemoryTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "100000",
                                     "AI_DM_MAX_RETRY_WAIT_SECONDS": "0"})
        env.start()
        self.addCleanup(env.stop)

    def generate(self, request, *, repair=False, budget=None):
        responses = [completion("{}")] if repair else []
        client = fake_client(*responses, completion(json.dumps(TURN)))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(budget or 100000)}):
            AIDungeonMaster(client).process_turn(request)
        return [call.kwargs["messages"] for call in client.chat.completions.create.call_args_list]

    def test_retrieves_old_public_and_private_events_in_chronological_order(self):
        request = request_with_history()
        before = request.model_copy(deep=True)
        messages, = self.generate(request)
        recent, memories = prompt_blocks(messages)
        self.assertEqual(recent, [history_record(entry) for entry in request.history[-10:]])
        self.assertEqual(memories, [
            {"history_position": index + 1, **history_record(request.history[index])}
            for index in (0, 2)
        ])
        self.assertEqual(request, before)
        self.assertIn("не раскрывай", messages[1]["content"])
        self.assertIn("текущее состояние ниже имеет приоритет", messages[1]["content"])

    def test_empty_short_and_unrelated_history_have_no_retrieved_events(self):
        for length in (0, 2, 10, 20):
            with self.subTest(length=length):
                request = turn_request()
                request.history = [record("Путники отдыхают.") for _ in range(length)]
                selection = select_history(request)
                self.assertEqual(len(selection.recent), min(10, length))
                self.assertEqual(selection.memories, ())

    def test_matches_action_names_items_location_and_private_notes(self):
        for query, location, old in (
            ("Мирослава, где травы?", "crypt", record("Мирослава торгует травами.")),
            ("Проверяю обсидиановый амулет.", "crypt", record("Обсидиановый амулет проклят.")),
            ("Осматриваюсь.", "Мельница", record("Мельница заброшена.")),
            ("Где тайник?", "crypt", record("Дверь закрыта.", notes="Тайник под лестницей.")),
        ):
            with self.subTest(query=query):
                request = request_with_history()
                request.player_action = query
                request.game_state.location = location
                request.history = [old, *request.history[-10:]]
                self.assertEqual(len(select_history(request).memories), 1)

    def test_word_matching_ignores_case_yo_punctuation_not_substrings_or_stopwords(self):
        request = request_with_history()
        request.history = [record("Чёрный КЛЮЧ!"), *request.history[-10:]]
        for query, found in (("черный ключ", True), ("ключница", False), ("ключом", False),
                             ("Я снова хочу это", False), ("123 !!!", False)):
            with self.subTest(query=query):
                request.player_action = query
                self.assertEqual(bool(select_history(request).memories), found)

    def test_action_matches_outrank_location_and_ties_prefer_recent_records(self):
        request = request_with_history()
        request.game_state.location = "Площадь"
        request.history = [record("Ключ у кузнеца."), record("Ключ у стража."),
                           record("Ключ у мельника."), record("Ключ у лодочника."),
                           record("Площадь залита солнцем."), *request.history[-10:]]
        selection = select_history(request)
        self.assertEqual([item["history_position"] for item in selection.memories], [4, 3, 2])
        self.assertEqual([item["history_position"] for item in selection.blocks(selection.count)[1]], [2, 3, 4])

    def test_rare_query_terms_rank_above_common_and_repetition_has_no_boost(self):
        request = request_with_history()
        request.player_action = "Обсидиановый ключ"
        request.history = [record("Обсидиановый амулет."), record("Ключ " * 100),
                           record("Ключ у мельника."), record("Ключ у лодочника."),
                           *request.history[-10:]]
        selection = select_history(request)
        self.assertEqual([item["history_position"] for item in selection.memories], [1, 4, 3])

    def test_exact_duplicate_records_are_not_retrieved_twice_or_from_recent_block(self):
        request = request_with_history()
        duplicate = record("Серебряный ключ спрятан.")
        request.history = [duplicate.model_copy(), duplicate.model_copy(), *request.history[-10:]]
        selection = select_history(request)
        self.assertEqual([item["history_position"] for item in selection.memories], [2])
        request.history[-1] = duplicate.model_copy()
        self.assertEqual(select_history(request).memories, ())

    def test_budget_drops_lower_ranked_memories_before_recent_history(self):
        request = request_with_history()
        before = request.model_copy(deep=True)
        # The older of two equally relevant records is the first to be dropped.
        only_best = request.model_copy(deep=True)
        only_best.history[0] = record("На площади тихо.")
        expected, = self.generate(only_best)
        actual, = self.generate(request, budget=message_chars(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(len(prompt_blocks(actual)[0]), 10)
        self.assertEqual([item["history_position"] for item in prompt_blocks(actual)[1]], [3])
        self.assertEqual(request, before)

    def test_budget_can_drop_all_memories_then_oldest_recent_entries(self):
        request = request_with_history()
        short = request.model_copy(deep=True)
        short.history = short.history[-2:]
        expected, = self.generate(short)
        actual, = self.generate(request, budget=message_chars(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(prompt_blocks(actual)[1], [])

    def test_repair_rechecks_budget_and_drops_memories_without_changing_raw_answer(self):
        request = request_with_history()
        request.history[0].narrative += " Подробности события." * 100
        initial, = self.generate(request)
        first, repair = self.generate(request, repair=True, budget=message_chars(initial))
        self.assertEqual(first, initial)
        self.assertEqual(len(prompt_blocks(repair)[0]), 10)
        self.assertEqual([item["history_position"] for item in prompt_blocks(repair)[1]], [3])
        self.assertEqual(repair[-2], {"role": "assistant", "content": "{}"})
        self.assertLessEqual(message_chars(repair), message_chars(initial))

    def test_required_context_overflow_still_fails_before_call(self):
        request = request_with_history()
        request.hidden_plot = "Тайна. " * 10000
        before = request.model_copy(deep=True)
        client = fake_client()
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "10000"}):
            with self.assertRaises(ContextBudgetError):
                AIDungeonMaster(client).process_turn(request)
        client.chat.completions.create.assert_not_called()
        self.assertEqual(request, before)

    def test_reused_dm_does_not_keep_memories_from_another_session(self):
        client = fake_client(completion(json.dumps(TURN)), completion(json.dumps(TURN)))
        dm = AIDungeonMaster(client)
        first = request_with_history()
        dm.process_turn(first)
        dm.process_turn(turn_request())
        initial, next_session = [call.kwargs["messages"] for call in client.chat.completions.create.call_args_list]
        self.assertEqual(len(prompt_blocks(initial)[1]), 2)
        self.assertEqual(prompt_blocks(next_session), ([], []))
        self.assertNotIn("верстаком", next_session[1]["content"])

    def test_orchestrator_passes_old_events_without_changing_history_or_public_output(self):
        request = request_with_history()
        # Same representation as history restored from JSON storage.
        history = [TurnHistoryEntry.model_validate_json(item.model_dump_json()) for item in request.history]
        before = [item.model_copy(deep=True) for item in history]
        game = session()
        player_action = action(game, 1)
        player_action = replace(player_action, text=request.player_action)
        client = fake_client(completion(json.dumps(TURN)))
        outcome = TurnOrchestrator(AIDungeonMaster(client), RulesService.from_text(DEMO_RULES)).play_turn(
            game.state, player_action, game.hidden_plot, Dice(SequenceSource([])), history=history,
        )
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.narration, TURN["narrative"])
        self.assertEqual(outcome.dm_notes, TURN["dm_thoughts"])
        self.assertEqual(outcome.state.revision, game.state.revision + 1)
        self.assertEqual(history, before)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(len(prompt_blocks(messages)[1]), 2)
        self.assertEqual(client.chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
