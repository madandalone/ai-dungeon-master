import unittest

from ai_dm.errors import AIDMError, ConfigurationError, ContextBudgetError, InvalidResponseError, ProviderError
from ai_dm.models import StateDelta
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError, TurnOrchestrator, UnmappableResponse
from integration.orchestrator import NO_RULES
from integration.tests.test_orchestrator import FakeDM, make_action, make_state, response
from rules_service import RulesUnavailableError

LOGGER = "integration.orchestrator"
SECRET_PLOT = "the innkeeper leads the cult"


class UnavailableRules:
    def get_rules(self, query):
        raise RulesUnavailableError("embedding model failed to load")


class BuggyRules:
    def get_rules(self, query):
        raise KeyError("a bug in the provider")


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
    def test_unavailable_rules_let_the_turn_continue_and_are_flagged(self):
        dm = FakeDM(response())
        with self.assertLogs(LOGGER, "WARNING") as logs:
            outcome = play(dm, UnavailableRules())
        self.assertTrue(outcome.accepted)
        self.assertTrue(outcome.rules_failed)
        self.assertEqual(dm.requests[0].rules_summary, NO_RULES)
        self.assertIn("rules unavailable", logs.output[0])
        self.assertIn("embedding model failed to load", logs.output[0])

    def test_a_bug_in_the_rules_provider_is_not_masked(self):
        dm = FakeDM(response())
        with self.assertRaises(KeyError):
            play(dm, BuggyRules())
        self.assertEqual(dm.requests, [])

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
            outcome = play(dm, UnavailableRules())
        self.assertFalse(outcome.accepted)
        self.assertTrue(outcome.rules_failed)


class DungeonMasterFailureTests(unittest.TestCase):
    def fail_with(self, error):
        with self.assertLogs(LOGGER, "ERROR"):
            with self.assertRaises(DungeonMasterError) as caught:
                play(FakeDM(error=error), StaticRules("rule"))
        self.assertIs(caught.exception.__cause__, error)
        return caught.exception

    def test_provider_error_keeps_category_and_retry_metadata(self):
        error = self.fail_with(ProviderError(category="rate_limit", status_code=429, retry_after=3.0, retryable=True))
        self.assertEqual((error.category, error.retryable, error.retry_after), ("rate_limit", True, 3.0))
        self.assertIn("429", str(error))

    def test_non_retryable_provider_error(self):
        error = self.fail_with(ProviderError(category="authentication", status_code=401))
        self.assertEqual((error.category, error.retryable, error.retry_after), ("authentication", False, None))

    def test_other_dm_errors_get_stable_categories(self):
        cases = [
            (ContextBudgetError(10, 5), "context_budget"),
            (ConfigurationError("no key"), "configuration"),
            (InvalidResponseError("bad json"), "invalid_response"),
            (AIDMError("something else"), "unknown"),
        ]
        for original, category in cases:
            with self.subTest(category=category):
                self.assertEqual(self.fail_with(original).category, category)

    def test_a_bug_in_the_dm_is_not_masked(self):
        with self.assertRaises(TypeError):
            play(FakeDM(error=TypeError("a bug in the DM")), StaticRules("rule"))


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
            play(FakeDM(response()), UnavailableRules(), action)
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

    def test_dm_failure_logs_the_category_not_the_message(self):
        with self.assertLogs(LOGGER, "ERROR") as logs:
            with self.assertRaises(DungeonMasterError):
                play(FakeDM(error=AIDMError(f"leaked {SECRET_PLOT}")), StaticRules("rule"))
        self.assertIn("category=unknown", logs.output[0])
        self.assertNotIn(SECRET_PLOT, logs.output[0])

    def test_unmappable_response_is_logged_and_still_raised(self):
        dm = FakeDM(response([StateDelta(character_name="Призрак", hp_change=-1)]))
        with self.assertLogs(LOGGER, "ERROR") as logs:
            with self.assertRaises(UnmappableResponse):
                play(dm, StaticRules("rule"))
        self.assertIn("turn unmappable", logs.output[0])


if __name__ == "__main__":
    unittest.main()
