import copy
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from ai_dm import evaluate_scenes
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import ProviderError
from ai_dm.main import DEMO_RULES
from ai_dm.memory import select_history
from ai_dm.models import TurnResponse
from ai_dm.scene_scenarios import scene_scenarios
from ai_dm.tests.test_dm_engine import TURN, UncheckedDM, completion, fake_client
from ai_dm.tests.test_history import action, session
from engine.dice import Dice, SequenceSource
from rules_service import RulesService


class SceneTests(unittest.TestCase):
    def test_scenarios_are_independent_and_old_clue_is_retrieved(self):
        cases = scene_scenarios()
        original = scene_scenarios()
        old = select_history(cases["old_clue"].request)
        self.assertEqual(len(old.recent), 10)
        self.assertEqual(len(old.memories), 1)
        self.assertIn("ящик", old.memories[0]["narrative"])
        cases["repeat_inspection"].request.history.clear()
        self.assertEqual(len(cases["npc_dialogue"].request.history), 1)
        self.assertEqual(scene_scenarios(), original)

    def test_each_scenario_fits_default_budget_and_keeps_its_context(self):
        # Stub responses prove wiring and contract compatibility, not narrative quality.
        with patch.dict("os.environ", {"AI_DM_MAX_CONTEXT_CHARS": "24000"}):
            for key, scenario in scene_scenarios().items():
                with self.subTest(case=key):
                    before = scenario.request.model_copy(deep=True)
                    client = fake_client(completion(json.dumps(TURN)))
                    AIDungeonMaster(client).process_turn(scenario.request)
                    messages = client.chat.completions.create.call_args.kwargs["messages"]
                    self.assertIn(scenario.request.player_action, messages[1]["content"])
                    self.assertIn(scenario.request.hidden_plot, messages[1]["content"])
                    self.assertEqual(scenario.request, before)
                    self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_accepted_notes_reach_next_turn_but_not_narration(self):
        notes = "Кузнец ждёт молот в обмен на серебряный ключ. Сделка ещё не заключена."
        response = {**TURN, "narrative": "Кузнец просит вернуть пропавший молот.", "dm_thoughts": notes}
        client = fake_client(completion(json.dumps(response)), completion(json.dumps(TURN)))
        dm = AIDungeonMaster(client)
        game = session()
        rules = RulesService.from_text(DEMO_RULES)
        first = game.play_turn(action(game, 1), dm, rules, Dice(SequenceSource([])))
        self.assertTrue(first.accepted)
        self.assertEqual(first.narration, response["narrative"])
        self.assertNotIn(notes, first.narration)
        self.assertEqual(game.history[-1].dm_notes, notes)
        game.play_turn(action(game, 2), dm, rules, Dice(SequenceSource([])))
        second_messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn(notes, second_messages[1]["content"])

    def test_rejected_scene_does_not_persist_proposed_notes(self):
        game = session()
        before = copy.deepcopy(game)
        response = {**TURN, "dm_thoughts": "Сделка заключена.",
                    "state_updates": [{"character_name": "Элиас", "items_removed": ["Молот"]}]}
        outcome = game.play_turn(action(game, 1), UncheckedDM(response),
                                 RulesService.from_text(DEMO_RULES), Dice(SequenceSource([])))
        self.assertFalse(outcome.accepted)
        self.assertIsNone(outcome.narration)
        self.assertEqual(game, before)


class EvaluationCommandTests(unittest.TestCase):
    def test_preview_never_creates_client_or_calls_llm(self):
        output = io.StringIO()
        with patch.object(evaluate_scenes, "AIDungeonMaster") as dm, redirect_stdout(output):
            self.assertEqual(evaluate_scenes.main([]), 0)
        dm.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertFalse(report["llm_called"])
        self.assertEqual(len(report["scenarios"]), 6)
        self.assertNotIn("response", report)

    def test_live_requires_explicit_case(self):
        with patch.object(evaluate_scenes, "AIDungeonMaster") as dm, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                evaluate_scenes.main(["--live"])
        self.assertEqual(caught.exception.code, 2)
        dm.assert_not_called()

    def test_generated_report_keeps_manual_review_pending(self):
        output = io.StringIO()
        with patch.object(evaluate_scenes, "AIDungeonMaster") as dm, redirect_stdout(output):
            dm.return_value.process_turn.return_value = TurnResponse(**TURN)
            self.assertEqual(evaluate_scenes.main(["--live", "--case", "old_clue"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["response"], TURN)
        self.assertEqual(report["review_status"], "pending")
        self.assertEqual(report["status"], "generated")
        self.assertEqual(len(report["prompt_sha256"]), 64)
        self.assertTrue(report["criteria"])
        dm.return_value.process_turn.assert_called_once()

    def test_failure_is_reported_without_provider_body_or_fake_response(self):
        output = io.StringIO()
        with patch.object(evaluate_scenes, "AIDungeonMaster") as dm, redirect_stdout(output):
            dm.return_value.process_turn.side_effect = ProviderError("PRIVATE PROVIDER BODY", category="quota")
            self.assertEqual(evaluate_scenes.main(["--live", "--case", "repeat_inspection"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["error"], {"type": "ProviderError", "category": "quota"})
        self.assertEqual(report["status"], "error")
        self.assertNotIn("response", report)
        self.assertNotIn("PRIVATE PROVIDER BODY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
