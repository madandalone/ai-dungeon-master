import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from ai_dm import main
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.main import DemoSession, HistoryAdapter
from ai_dm.models import TurnHistoryEntry, TurnRequest, TurnResponse
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request
from engine.dice import Dice, SequenceSource
from engine.models import Character, GameState, InventoryItem, PlayerAction
from integration import DungeonMasterError
from rules_service import RulesService


def entry(number):
    return TurnHistoryEntry(
        player_action=f"Действие {number}",
        narrative=f"Событие {number}",
        dm_notes=f"Секрет {number}",
    )


def session(session_id="session"):
    return DemoSession(
        state=GameState(
            session_id, 0, 0, "crypt",
            (Character("hero", "Элиас", "Плут", 12, 12, {},
                       (InventoryItem("dagger", "Кинжал", 1),)),), {},
        ),
        hidden_plot="Скрытый сюжет",
        room_context="Старая комната",
    )


def action(game, number):
    return PlayerAction(
        f"action-{number}", game.state.session_id, "hero",
        f"Осмотр: действие {number}", game.state.revision,
    )


class HistoryPromptTests(unittest.TestCase):
    def test_old_request_without_history_and_independent_defaults(self):
        first = turn_request()
        second = turn_request()
        first.history.append(entry(1))
        self.assertEqual(second.history, [])
        self.assertEqual(TurnRequest.model_validate(second.model_dump(exclude={"history"})).history, [])
        self.assertEqual(TurnHistoryEntry(player_action="Осмотр", narrative="Пусто").dm_notes, "")

    def test_last_ten_entries_in_chronological_order_without_mutating_request(self):
        request = turn_request()
        request.history = [entry(i) for i in range(12)]
        before = request.model_copy(deep=True)
        client = fake_client(completion(json.dumps(TURN)))
        AIDungeonMaster(client).process_turn(request)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        history_text = prompt.split("### ИСТОРИЯ ПРИНЯТЫХ ХОДОВ (от ранних к поздним):\n", 1)[1]
        history, _ = json.JSONDecoder().raw_decode(history_text)
        self.assertEqual(history, [item.model_dump() for item in request.history[-10:]])
        self.assertIn("текущее состояние ниже имеет приоритет", prompt)
        self.assertIn("не раскрывай", prompt)
        self.assertIn(request.game_state.location, prompt)
        self.assertEqual(request, before)

    def test_reused_dm_does_not_remember_previous_request(self):
        client = fake_client(completion(json.dumps(TURN)), completion(json.dumps(TURN)))
        dm = AIDungeonMaster(client)
        first = turn_request()
        first.history = [entry("unique-private-history")]
        dm.process_turn(first)
        dm.process_turn(turn_request())
        prompts = [call.kwargs["messages"][1]["content"]
                   for call in client.chat.completions.create.call_args_list]
        self.assertIn("unique-private-history", prompts[0])
        self.assertNotIn("unique-private-history", prompts[1])

    def test_history_survives_format_repair(self):
        client = fake_client(completion("{}"), completion(json.dumps(TURN)))
        request = turn_request()
        request.history = [entry("private-memory")]
        AIDungeonMaster(client).process_turn(request)
        calls = client.chat.completions.create.call_args_list
        self.assertEqual(calls[0].kwargs["messages"][:2], calls[1].kwargs["messages"][:2])
        self.assertIn("private-memory", calls[1].kwargs["messages"][1]["content"])
        self.assertEqual(len(request.history), 1)


class HistoryAdapterTests(unittest.TestCase):
    def test_adapter_uses_snapshot_and_does_not_mutate_original_request(self):
        history = [entry(1)]
        dm = Mock()
        dm.process_turn.return_value = TurnResponse(**TURN)
        adapter = HistoryAdapter(dm, history)
        history[0].dm_notes = "changed after snapshot"
        history.append(entry(2))
        request = turn_request()
        request.history = [entry("original")]
        before = request.model_copy(deep=True)
        adapter.process_turn(request)
        enriched = dm.process_turn.call_args.args[0]
        self.assertEqual(enriched.history, [entry(1)])
        self.assertEqual(request, before)
        self.assertIsNot(enriched.game_state, request.game_state)
        # Even a delegate changing the enriched history cannot change the next call.
        enriched.history[0].dm_notes = "changed by DM"
        adapter.process_turn(request)
        self.assertEqual(dm.process_turn.call_args.args[0].history, [entry(1)])


class DemoSessionTests(unittest.TestCase):
    def setUp(self):
        self.rules = RulesService.from_text(main.DEMO_RULES)
        self.dice = Dice(SequenceSource([]))

    def test_two_turns_receive_updated_state_and_only_accepted_history(self):
        first = {
            **TURN,
            "new_location": "Подвал",
            "state_updates": [{
                "character_name": "Элиас", "hp_change": -3,
                "items_added": ["Ключ"], "items_removed": ["Кинжал"],
            }],
        }
        client = fake_client(completion(json.dumps(first)), completion(json.dumps(TURN)))
        real_dm = AIDungeonMaster(client)
        requests = []

        def capture(request):
            requests.append(request.model_copy(deep=True))
            return real_dm.process_turn(request)

        dm = Mock()
        dm.process_turn.side_effect = capture
        game = session()
        first_action = action(game, 1)
        out1 = game.play_turn(first_action, dm, self.rules, self.dice)
        self.assertTrue(out1.accepted)
        self.assertIs(game.state, out1.state)
        self.assertEqual(game.room_context, "")
        out2 = game.play_turn(action(game, 2), dm, self.rules, self.dice)
        self.assertTrue(out2.accepted)
        self.assertEqual((game.state.revision, game.state.turn_number), (2, 2))
        self.assertEqual(requests[0].history, [])
        self.assertIn("Осмотр", requests[0].rules_summary)
        self.assertEqual(requests[1].game_state.location, "Подвал")
        self.assertEqual(requests[1].game_state.room_context, "")
        self.assertEqual(requests[1].game_state.active_characters[0].hp, 9)
        self.assertEqual(requests[1].game_state.active_characters[0].inventory, ["Ключ"])
        self.assertEqual(requests[1].history, [TurnHistoryEntry(
            player_action=first_action.text,
            narrative=first["narrative"],
            dm_notes=first["dm_thoughts"],
        )])
        self.assertEqual(len(game.history), 2)

    def test_rejected_turn_preserves_state_context_and_history(self):
        game = session()
        game.history = [entry("accepted")]
        before = copy.deepcopy(game)
        invalid = {
            **TURN,
            "new_location": "Недоступный зал",
            "state_updates": [{"character_name": "Элиас", "items_removed": ["Меч"]}],
        }
        dm = AIDungeonMaster(fake_client(completion(json.dumps(invalid))))
        result = game.play_turn(action(game, 2), dm, self.rules, self.dice)
        self.assertFalse(result.accepted)
        self.assertIs(result.state, game.state)
        self.assertIsNone(result.narration)
        self.assertEqual(game, before)

    def test_llm_failure_preserves_state_context_and_history(self):
        game = session()
        game.history = [entry("accepted")]
        before = copy.deepcopy(game)
        dm = AIDungeonMaster(fake_client(TimeoutError("offline")))
        with self.assertRaises(DungeonMasterError):
            game.play_turn(action(game, 2), dm, self.rules, self.dice)
        self.assertEqual(game, before)

    def test_two_sessions_do_not_share_history_with_one_dm(self):
        dm = Mock()
        dm.process_turn.return_value = TurnResponse(**TURN)
        first, second = session("first"), session("second")
        first.history.append(entry("first-only"))
        first.play_turn(action(first, 1), dm, self.rules, self.dice)
        second.play_turn(action(second, 1), dm, self.rules, self.dice)
        first_request, second_request = [call.args[0] for call in dm.process_turn.call_args_list]
        self.assertEqual(first_request.history, [entry("first-only")])
        self.assertEqual(second_request.history, [])
        self.assertEqual(len(first.history), 2)
        self.assertEqual(len(second.history), 1)
        self.assertEqual(first.room_context, "Старая комната")
        self.assertEqual(second.room_context, "Старая комната")

    def test_demo_runs_campaign_and_two_turns_without_printing_private_notes(self):
        first = {
            **TURN,
            "new_location": "Подвал",
            "state_updates": [{"character_name": "Элиас", "hp_change": -2}],
        }
        client = fake_client(
            completion(json.dumps(CAMPAIGN)),
            completion(json.dumps(first)),
            completion(json.dumps(TURN)),
        )
        output = io.StringIO()
        with patch.object(main, "AIDungeonMaster", return_value=AIDungeonMaster(client)), \
                patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value=None), \
                redirect_stdout(output):
            main.run()
        self.assertEqual(client.chat.completions.create.call_count, 3)
        text = output.getvalue()
        self.assertIn("Ход 1.", text)
        self.assertIn("Ход 2.", text)
        self.assertIn("Здоровье 10/12", text)
        self.assertNotIn(CAMPAIGN["hidden_plot"], text)
        self.assertNotIn(TURN["dm_thoughts"], text)
        second_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn(TURN["dm_thoughts"], second_prompt)
        self.assertIn("Подвал", second_prompt)


if __name__ == "__main__":
    unittest.main()
