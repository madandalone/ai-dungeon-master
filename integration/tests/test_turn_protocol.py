"""The turn protocol end to end: client action_id/expected_revision + executor + orchestrator + real Engine.

`BackendSim` mirrors what a Backend turn handler does with these pieces, so the tests are the contract:
which status a client sees, how often the DM is paid for, and what survives a retry or a race.
"""

import threading
import unittest

from ai_dm.errors import ProviderError
from ai_dm.models import StateDelta, TurnResponse
from engine.dice import Dice, SequenceSource
from engine.models import Character, GameState
from integration import IdempotentExecutor, TurnOrchestrator, make_player_action

HERO = Character("hero", "Hero", "Rogue", 10, 10, {}, ())


class CountingDM:
    def __init__(self, delay=0.0, fail_first=False, hp_change=-1, removes=()):
        self.calls, self.delay, self.fail_first, self.hp_change, self.removes = 0, delay, fail_first, hp_change, removes
        self._lock = threading.Lock()

    def process_turn(self, request):
        with self._lock:
            self.calls += 1
            number = self.calls
        threading.Event().wait(self.delay)
        if self.fail_first and number == 1:
            raise ProviderError(category="unavailable", retryable=True)
        update = StateDelta(character_name="Hero", hp_change=self.hp_change, items_removed=list(self.removes))
        return TurnResponse(narrative="ok", state_updates=[update], new_location=None, dm_thoughts="")


class Rules:
    def get_rules(self, query):
        return "rule"


class BackendSim:
    def __init__(self, dm):
        self.orchestrator = TurnOrchestrator(dm, Rules())
        self.executor = IdempotentExecutor()
        self.state = GameState("s1", 0, 0, "start", (HERO,), {})
        self.history = []

    def turn(self, text="open the door", action_id=None, expected_revision=None):
        def play():
            action = make_player_action(self.state, "hero", text, action_id, expected_revision)
            outcome = self.orchestrator.play_turn(self.state, action, "plot", Dice(SequenceSource([10])), history=self.history)
            if outcome.accepted:
                self.state = outcome.state
            stale = any(error.code == "STALE_REVISION" for error in outcome.errors)
            return {"status": 409 if stale else 200, "accepted": outcome.accepted, "revision": self.state.revision,
                    "errors": [e.code for e in outcome.errors]}

        body, replayed = self.executor.run("s1", action_id or text, play, remember=lambda b: b["accepted"])
        return {**body, "replayed": replayed}

    @property
    def hp(self):
        return self.state.characters[0].hp


class RetryTests(unittest.TestCase):
    def test_retry_with_the_same_action_id_changes_the_game_once_and_calls_the_dm_once(self):
        dm = CountingDM()
        backend = BackendSim(dm)
        first = backend.turn(action_id="a-1", expected_revision=0)
        retry = backend.turn(action_id="a-1", expected_revision=0)
        self.assertEqual((first["accepted"], first["replayed"]), (True, False))
        self.assertEqual((retry["accepted"], retry["replayed"], retry["revision"]), (True, True, 1))
        self.assertEqual((dm.calls, backend.hp, backend.state.revision), (1, 9, 1))

    def test_a_failed_dm_call_can_be_retried_with_the_same_action_id(self):
        dm = CountingDM(fail_first=True)
        backend = BackendSim(dm)
        with self.assertRaises(Exception):
            backend.turn(action_id="a-1", expected_revision=0)
        retry = backend.turn(action_id="a-1", expected_revision=0)
        self.assertEqual((retry["accepted"], retry["replayed"], backend.hp, dm.calls), (True, False, 9, 2))

    def test_a_turn_the_engine_rejected_is_not_remembered(self):
        dm = CountingDM(removes=["Sword"])
        backend = BackendSim(dm)
        for _ in range(2):
            result = backend.turn(action_id="a-1", expected_revision=0)
            self.assertEqual((result["accepted"], result["replayed"], result["errors"]), (False, False, ["INVALID_ITEM"]))
        self.assertEqual((dm.calls, backend.state.revision), (2, 0))

    def test_distinct_actions_in_a_row_are_all_applied(self):
        backend = BackendSim(CountingDM())
        backend.turn(action_id="a-1", expected_revision=0)
        second = backend.turn(action_id="a-2", expected_revision=1)
        self.assertEqual((second["accepted"], backend.state.revision, backend.hp), (True, 2, 8))


class StaleRevisionTests(unittest.TestCase):
    def test_a_stale_client_gets_409_and_never_costs_an_llm_call(self):
        dm = CountingDM()
        backend = BackendSim(dm)
        backend.turn(action_id="a-1", expected_revision=0)
        stale = backend.turn(action_id="a-2", expected_revision=0)
        self.assertEqual((stale["status"], stale["accepted"], stale["errors"]), (409, False, ["STALE_REVISION"]))
        self.assertEqual((dm.calls, backend.state.revision, backend.hp), (1, 1, 9))

    def test_a_client_that_sends_no_revision_is_treated_as_up_to_date(self):
        backend = BackendSim(CountingDM())
        backend.turn(action_id="a-1", expected_revision=0)
        self.assertTrue(backend.turn(action_id="a-2")["accepted"])

    def test_a_stale_retry_succeeds_after_the_client_refreshes_its_revision(self):
        backend = BackendSim(CountingDM())
        backend.turn(action_id="a-1", expected_revision=0)
        self.assertEqual(backend.turn(action_id="a-2", expected_revision=0)["status"], 409)
        self.assertTrue(backend.turn(action_id="a-2", expected_revision=1)["accepted"])


class RaceTests(unittest.TestCase):
    def test_two_players_acting_at_once_one_wins_and_the_other_is_told_to_refresh(self):
        dm = CountingDM(delay=0.15)
        backend = BackendSim(dm)
        results = []
        threads = [
            threading.Thread(target=lambda i=i: results.append(backend.turn(action_id=f"p{i}", expected_revision=0)))
            for i in range(2)
        ]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(sorted((r["status"], r["accepted"]) for r in results), [(200, True), (409, False)])
        self.assertEqual((backend.state.revision, backend.hp), (1, 9))
        self.assertEqual(dm.calls, 1, "the loser is rejected before the LLM is called")

    def test_a_retry_racing_the_original_request_does_not_double_apply(self):
        dm = CountingDM(delay=0.15)
        backend = BackendSim(dm)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(backend.turn(action_id="a-1", expected_revision=0)))
            for _ in range(3)
        ]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(sorted(r["replayed"] for r in results), [False, True, True])
        self.assertTrue(all(r["accepted"] for r in results))
        self.assertEqual((dm.calls, backend.state.revision, backend.hp), (1, 1, 9))


if __name__ == "__main__":
    unittest.main()
