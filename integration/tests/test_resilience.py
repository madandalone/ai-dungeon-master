import unittest

from ai_dm.models import StateDelta
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError, TurnOrchestrator, UnmappableResponse
from integration.orchestrator import NO_RULES
from integration.tests.test_orchestrator import FakeDM, make_action, make_state, response

LOGGER = "integration.orchestrator"
SECRET_PLOT = "the innkeeper leads the cult"


class BrokenRules:
    def get_rules(self, query):
        raise RuntimeError("embedding model failed to load")


class StaticRules:
    def __init__(self, text):
        self.text = text

    def get_rules(self, query):
        return self.text


def play(dm, rules, action=None):
    return TurnOrchestrator(dm, rules).play_turn(
        make_state(), action or make_action(), SECRET_PLOT, Dice(SequenceSource([10]))
    )


class RulesFailureTests(unittest.TestCase):
    def test_turn_continues_without_rules_and_is_flagged(self):
        dm = FakeDM(response())
        with self.assertLogs(LOGGER, "WARNING") as logs:
            outcome = play(dm, BrokenRules())
        self.assertTrue(outcome.accepted)
        self.assertTrue(outcome.rules_failed)
        self.assertEqual(dm.requests[0].rules_summary, NO_RULES)
        self.assertIn("rules unavailable", logs.output[0])
        self.assertIn("embedding model failed to load", logs.output[0])

    def test_empty_rules_are_not_a_failure(self):
        dm = FakeDM(response())
        outcome = play(dm, StaticRules(""))
        self.assertFalse(outcome.rules_failed)
        self.assertEqual(dm.requests[0].rules_summary, NO_RULES)

    def test_healthy_rules_do_not_set_the_flag(self):
        self.assertFalse(play(FakeDM(response()), StaticRules("some rule")).rules_failed)

    def test_rules_failure_is_kept_on_an_engine_rejection(self):
        dm = FakeDM(response([StateDelta(character_name="Элиас", items_removed=["Меч"])]))
        with self.assertLogs(LOGGER, "WARNING"):
            outcome = play(dm, BrokenRules())
        self.assertFalse(outcome.accepted)
        self.assertTrue(outcome.rules_failed)


class LoggingTests(unittest.TestCase):
    def test_accepted_turn_logs_identifiers_and_timings(self):
        with self.assertLogs(LOGGER, "INFO") as logs:
            play(FakeDM(response()), StaticRules("rule"))
        line = logs.output[0]
        for part in ("session=s1", "action=a1", "accepted=True", "errors=-", "commands=0", "rules_ms=", "dm_ms="):
            self.assertIn(part, line)

    def test_logs_never_contain_player_text_narration_or_the_hidden_plot(self):
        action = make_action(text="I whisper the password to the innkeeper")
        with self.assertLogs(LOGGER, "INFO") as logs:
            play(FakeDM(response()), BrokenRules(), action)
        joined = "\n".join(logs.output)
        for secret in (SECRET_PLOT, "password", "Стрела попадает в плечо.", "secret note"):
            self.assertNotIn(secret, joined)

    def test_engine_rejection_logs_error_codes(self):
        dm = FakeDM(response([StateDelta(character_name="Элиас", items_removed=["Меч"])]))
        with self.assertLogs(LOGGER, "INFO") as logs:
            play(dm, StaticRules("rule"))
        self.assertIn("accepted=False", logs.output[0])
        self.assertIn("errors=INVALID_ITEM", logs.output[0])

    def test_stale_revision_is_logged_and_never_reaches_the_dm(self):
        dm = FakeDM(response())
        with self.assertLogs(LOGGER, "INFO") as logs:
            play(dm, StaticRules("rule"), make_action(revision=1))
        self.assertIn("errors=STALE_REVISION", logs.output[0])
        self.assertIn("rejected before DM call", logs.output[0])
        self.assertEqual(dm.requests, [])

    def test_dm_failure_logs_the_error_type_not_the_message(self):
        with self.assertLogs(LOGGER, "ERROR") as logs:
            with self.assertRaises(DungeonMasterError):
                play(FakeDM(error=TimeoutError(f"leaked {SECRET_PLOT}")), StaticRules("rule"))
        self.assertIn("error=TimeoutError", logs.output[0])
        self.assertNotIn(SECRET_PLOT, logs.output[0])

    def test_dm_error_message_is_safe_to_show_but_keeps_the_cause(self):
        original = TimeoutError(f"leaked {SECRET_PLOT}")
        with self.assertLogs(LOGGER, "ERROR"):
            with self.assertRaises(DungeonMasterError) as caught:
                play(FakeDM(error=original), StaticRules("rule"))
        self.assertEqual(str(caught.exception), "Dungeon Master failed (TimeoutError)")
        self.assertIs(caught.exception.__cause__, original)

    def test_unmappable_response_is_logged_and_still_raised(self):
        dm = FakeDM(response([StateDelta(character_name="Призрак", hp_change=-1)]))
        with self.assertLogs(LOGGER, "ERROR") as logs:
            with self.assertRaises(UnmappableResponse):
                play(dm, StaticRules("rule"))
        self.assertIn("turn unmappable", logs.output[0])


if __name__ == "__main__":
    unittest.main()
