import copy
import json
import os
import unittest
from unittest.mock import patch

from ai_dm import dm_engine
from ai_dm.context import message_chars
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ConfigurationError, ContextBudgetError, InvalidResponseError
from ai_dm.models import CampaignStartRequest, TurnResponse
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request
from ai_dm.tests.test_history import action, entry, session
from ai_dm.main import DEMO_RULES
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError, TurnOrchestrator
from rules_service import RulesService


def history_in(messages):
    text = messages[1]["content"].split(
        "### ИСТОРИЯ ПРИНЯТЫХ ХОДОВ (от ранних к поздним):\n", 1
    )[1]
    return json.JSONDecoder().raw_decode(text)[0]


def size_of(messages):
    # Measure the actual outgoing message representation, including JSON escaping.
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "AI_DM_MAX_CONTEXT_CHARS": "100000",
            "AI_DM_MAX_OUTPUT_TOKENS": "333",
        })
        env.start()
        self.addCleanup(env.stop)
        monster = patch.object(dm_engine.DnD5eClient, "get_monster", return_value={"name": "Skeleton"})
        monster.start()
        self.addCleanup(monster.stop)

    def invoke(self, client, request=None, *, campaign=False):
        dm = AIDungeonMaster(client)
        if campaign:
            return dm.generate_campaign_start(
                request or CampaignStartRequest(setting_theme="Крипта", characters=[])
            )
        return dm.process_turn(request or turn_request())

    def messages_for(self, request=None, *, campaign=False, repair=False):
        data = CAMPAIGN if campaign else TURN
        responses = [completion("{}")] if repair else []
        client = fake_client(*responses, completion(json.dumps(data)))
        self.invoke(client, request, campaign=campaign)
        return [call.kwargs["messages"] for call in client.chat.completions.create.call_args_list]

    def test_exact_boundary_includes_schema_roles_and_escaped_unicode(self):
        request = turn_request()
        request.player_action = 'Осмотр: "руны"\n\\тайник 🗝'
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                target = None if campaign else request
                baseline = self.messages_for(target, campaign=campaign)[0]
                budget = size_of(baseline)
                self.assertGreater(budget, sum(len(message["content"]) for message in baseline))
                client = fake_client(completion(json.dumps(CAMPAIGN if campaign else TURN)))
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(budget)}):
                    self.invoke(client, target, campaign=campaign)
                self.assertEqual(client.chat.completions.create.call_args.kwargs["messages"], baseline)
                client = fake_client()
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(budget - 1)}):
                    with self.assertRaises(ContextBudgetError) as caught:
                        self.invoke(client, target, campaign=campaign)
                client.chat.completions.create.assert_not_called()
                self.assertEqual(caught.exception.required_chars, budget)
                self.assertFalse(caught.exception.repair)
                self.assertNotIn("Скрытый", str(caught.exception))

    def test_keeps_latest_whole_entries_and_all_required_context(self):
        request = turn_request()
        request.history = [entry(i) for i in range(12)]
        request.history[-1].narrative += " Последнее событие." * 50
        before = request.model_copy(deep=True)
        recent = request.model_copy(deep=True)
        recent.history = recent.history[-3:]
        expected = self.messages_for(recent)[0]
        client = fake_client(completion(json.dumps(TURN)))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(size_of(expected))}):
            self.invoke(client, request)
        actual = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(actual, expected)
        self.assertEqual(history_in(actual), [item.model_dump() for item in before.history[-3:]])
        self.assertIn(request.rules_summary, actual[1]["content"])
        self.assertIn(request.hidden_plot, actual[1]["content"])
        self.assertIn(request.player_action, actual[1]["content"])
        self.assertIn(request.game_state.room_context, actual[1]["content"])
        self.assertIn(json.dumps([c.model_dump() for c in request.game_state.active_characters],
                                 ensure_ascii=False), actual[1]["content"])
        self.assertIn(json.dumps(TurnResponse.model_json_schema(), ensure_ascii=False, indent=2),
                      actual[1]["content"])
        self.assertEqual(request, before)

    def test_oversized_latest_entry_does_not_get_replaced_by_older_short_entries(self):
        request = turn_request()
        empty_messages = self.messages_for(request)[0]
        request.history = [entry(1), entry(2)]
        request.history[-1].narrative = "Последнее событие. " * 1000
        before = request.model_copy(deep=True)
        client = fake_client(completion(json.dumps(TURN)))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(size_of(empty_messages) + 1000)}):
            self.invoke(client, request)
        actual = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(actual, empty_messages)
        self.assertEqual(request, before)

    def test_large_budget_still_limits_history_to_ten(self):
        request = turn_request()
        request.history = [entry(i) for i in range(15)]
        messages = self.messages_for(request)[0]
        self.assertEqual(history_in(messages), [item.model_dump() for item in request.history[-10:]])

    def test_each_required_field_can_prevent_call_even_after_dropping_history(self):
        for field in ("rules_summary", "hidden_plot", "player_action", "room_context", "inventory"):
            with self.subTest(field=field):
                request = turn_request()
                request.history = [entry(1)]
                private = "Непубличный контекст " * 1000
                if field == "room_context":
                    request.game_state.room_context = private
                elif field == "inventory":
                    request.game_state.active_characters[0].inventory = [private]
                else:
                    setattr(request, field, private)
                before = request.model_copy(deep=True)
                client = fake_client()
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "10000"}):
                    with self.assertRaises(ContextBudgetError) as caught:
                        self.invoke(client, request)
                client.chat.completions.create.assert_not_called()
                self.assertNotIn("Непубличный", str(caught.exception))
                self.assertEqual(request, before)

    def test_repair_drops_old_history_without_cutting_bad_answer_or_error_details(self):
        request = turn_request()
        request.history = [entry(i) for i in range(4)]
        for item in request.history:
            item.narrative *= 200
        initial, full_repair = self.messages_for(request, repair=True)
        budget = size_of(initial)
        self.assertGreater(size_of(full_repair), budget)
        before = request.model_copy(deep=True)
        client = fake_client(completion("{}"), completion(json.dumps(TURN)))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(budget)}):
            self.invoke(client, request)
        first, second = [call.kwargs["messages"] for call in client.chat.completions.create.call_args_list]
        self.assertEqual(first, initial)
        self.assertEqual(history_in(second), [item.model_dump() for item in request.history[-3:]])
        self.assertEqual(second[2:], full_repair[2:])
        self.assertEqual(second[-2], {"role": "assistant", "content": "{}"})
        self.assertLessEqual(size_of(second), budget)
        self.assertEqual(request, before)

    def test_repair_at_exact_boundary_for_both_operations(self):
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                expected = self.messages_for(campaign=campaign, repair=True)[1]
                data = CAMPAIGN if campaign else TURN
                client = fake_client(completion("{}"), completion(json.dumps(data)))
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(size_of(expected))}):
                    self.invoke(client, campaign=campaign)
                self.assertEqual(client.chat.completions.create.call_args.kwargs["messages"], expected)

    def test_oversized_repair_stops_after_one_call_for_both_operations(self):
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                expected = self.messages_for(campaign=campaign, repair=True)[1]
                client = fake_client(completion("{}"))
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": str(size_of(expected) - 1)}):
                    with self.assertRaises(ContextBudgetError) as caught:
                        self.invoke(client, campaign=campaign)
                self.assertTrue(caught.exception.repair)
                self.assertEqual(caught.exception.required_chars, size_of(expected))
                self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_huge_provider_response_is_not_truncated_to_force_a_repair_request(self):
        client = fake_client(completion("Непубличный ответ " * 3000))
        with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "10000"}):
            with self.assertRaises(ContextBudgetError) as caught:
                self.invoke(client)
        self.assertTrue(caught.exception.repair)
        self.assertNotIn("Непубличный", str(caught.exception))
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_positive_output_cap_is_used_on_campaign_turn_and_repair(self):
        for campaign in (False, True):
            with self.subTest(campaign=campaign):
                data = CAMPAIGN if campaign else TURN
                client = fake_client(completion("{}"), completion(json.dumps(data)))
                self.invoke(client, campaign=campaign)
                for call in client.chat.completions.create.call_args_list:
                    self.assertEqual(call.kwargs["max_tokens"], 333)
                    self.assertIn("Пиши компактно", call.kwargs["messages"][0]["content"])

    def test_invalid_limits_fail_before_llm_request(self):
        for name in ("AI_DM_MAX_CONTEXT_CHARS", "AI_DM_MAX_OUTPUT_TOKENS"):
            for value in ("", "0", "-1", "1.5", "nan", "inf", "abc"):
                for campaign in (False, True):
                    with self.subTest(name=name, value=value, campaign=campaign):
                        client = fake_client()
                        with patch.dict(os.environ, {name: value}):
                            with self.assertRaises(ConfigurationError):
                                self.invoke(client, campaign=campaign)
                        client.chat.completions.create.assert_not_called()

    def test_settings_are_read_again_on_next_operation(self):
        client = fake_client(completion(json.dumps(TURN)), completion(json.dumps(TURN)))
        dm = AIDungeonMaster(client)
        dm.process_turn(turn_request())
        with patch.dict(os.environ, {"AI_DM_MAX_OUTPUT_TOKENS": "777"}):
            dm.process_turn(turn_request())
        self.assertEqual([call.kwargs["max_tokens"] for call in client.chat.completions.create.call_args_list],
                         [333, 777])

    def test_reported_length_limit_triggers_only_one_repair_with_same_cap(self):
        for campaign in (False, True):
            data = CAMPAIGN if campaign else TURN
            for repaired in (True, False):
                with self.subTest(campaign=campaign, repaired=repaired):
                    truncated = completion(json.dumps(data))
                    truncated.choices[0].finish_reason = "length"
                    second = completion(json.dumps(data)) if repaired else truncated
                    client = fake_client(truncated, second)
                    if repaired:
                        self.invoke(client, campaign=campaign)
                    else:
                        with self.assertRaises(InvalidResponseError):
                            self.invoke(client, campaign=campaign)
                    calls = client.chat.completions.create.call_args_list
                    self.assertEqual(len(calls), 2)
                    self.assertIn("лимит токенов", calls[1].kwargs["messages"][-1]["content"])
                    self.assertEqual(calls[1].kwargs["max_tokens"], 333)

    def test_budget_errors_preserve_demo_state_and_history(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                game = session()
                game.history = [entry(1)]
                before = copy.deepcopy(game)
                client = fake_client(completion("Слишком длинный ответ " * 2000)) if repair else fake_client()
                with patch.dict(os.environ, {"AI_DM_MAX_CONTEXT_CHARS": "10000" if repair else "1"}):
                    with self.assertRaises(DungeonMasterError) as caught:
                        game.play_turn(action(game, 1), AIDungeonMaster(client),
                                       RulesService.from_text(DEMO_RULES), Dice(SequenceSource([])))
                self.assertIsInstance(caught.exception.__cause__, ContextBudgetError)
                self.assertEqual(game, before)
                self.assertEqual(client.chat.completions.create.call_count, int(repair))

    def test_direct_orchestrator_history_remains_compatible(self):
        game = session()
        history = [entry(i) for i in range(12)]
        before = copy.deepcopy(history)
        client = fake_client(completion(json.dumps(TURN)))
        result = TurnOrchestrator(AIDungeonMaster(client), RulesService.from_text(DEMO_RULES)).play_turn(
            game.state, action(game, 1), game.hidden_plot, Dice(SequenceSource([])),
            room_context=game.room_context, history=history,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.state.revision, game.state.revision + 1)
        self.assertEqual(history, before)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(history_in(messages), [item.model_dump() for item in history[-10:]])

    def test_message_measurement_uses_characters_not_utf8_bytes(self):
        messages = [{"role": "user", "content": 'ё🗝"\n\\'}]
        wire = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(message_chars(messages), len(wire))
        self.assertLess(message_chars(messages), len(wire.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
