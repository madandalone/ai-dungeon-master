import threading
import unittest

from engine.models import Character, GameState
from integration import IdempotentExecutor, make_player_action


def state(revision=4):
    hero = Character("c1", "Hero", "Rogue", 10, 10, {}, ())
    return GameState("s1", revision, revision, "start", (hero,), {})


class MakePlayerActionTests(unittest.TestCase):
    def test_client_values_are_kept(self):
        action = make_player_action(state(4), "c1", "open", action_id="a-1", expected_revision=2)
        self.assertEqual((action.action_id, action.expected_revision), ("a-1", 2))
        self.assertEqual((action.session_id, action.character_id, action.text), ("s1", "c1", "open"))

    def test_legacy_client_without_values_gets_a_fresh_id_and_the_current_revision(self):
        first = make_player_action(state(4), "c1", "open")
        second = make_player_action(state(4), "c1", "open")
        self.assertEqual(first.expected_revision, 4)
        self.assertNotEqual(first.action_id, second.action_id)

    def test_revision_zero_is_a_real_value_not_missing(self):
        self.assertEqual(make_player_action(state(4), "c1", "open", expected_revision=0).expected_revision, 0)

    def test_empty_action_id_is_treated_as_missing(self):
        self.assertTrue(make_player_action(state(), "c1", "open", action_id="").action_id)


class ExecutorTests(unittest.TestCase):
    def test_repeated_key_replays_without_calling_fn(self):
        executor, calls = IdempotentExecutor(), []
        run = lambda: executor.run("s", "k", lambda: calls.append(1) or "result")
        self.assertEqual(run(), ("result", False))
        self.assertEqual(run(), ("result", True))
        self.assertEqual(len(calls), 1)

    def test_same_key_in_another_session_is_independent(self):
        executor = IdempotentExecutor()
        self.assertEqual(executor.run("s1", "k", lambda: "one"), ("one", False))
        self.assertEqual(executor.run("s2", "k", lambda: "two"), ("two", False))

    def test_results_that_are_not_worth_remembering_run_again(self):
        executor, calls = IdempotentExecutor(), []
        for _ in range(2):
            executor.run("s", "k", lambda: calls.append(1) or {"accepted": False}, remember=lambda r: r["accepted"])
        self.assertEqual(len(calls), 2)

    def test_exception_stores_nothing_and_releases_the_lock(self):
        executor = IdempotentExecutor()
        with self.assertRaises(RuntimeError):
            executor.run("s", "k", lambda: (_ for _ in ()).throw(RuntimeError("provider down")))
        self.assertEqual(executor.run("s", "k", lambda: "ok"), ("ok", False))

    def test_oldest_results_are_forgotten_beyond_the_bound(self):
        executor = IdempotentExecutor(max_remembered=2)
        for key in ("a", "b", "c"):
            executor.run("s", key, lambda key=key: key)
        self.assertEqual(executor.run("s", "c", lambda: "again"), ("c", True))
        self.assertEqual(executor.run("s", "b", lambda: "again"), ("b", True))
        self.assertEqual(executor.run("s", "a", lambda: "again"), ("again", False))

    def test_invalid_bound_is_rejected(self):
        with self.assertRaises(ValueError):
            IdempotentExecutor(max_remembered=0)

    def test_forget_drops_a_sessions_results(self):
        executor = IdempotentExecutor()
        executor.run("s", "k", lambda: "old")
        executor.forget("s")
        self.assertEqual(executor.run("s", "k", lambda: "new"), ("new", False))
        executor.forget("never-existed")


class ConcurrencyTests(unittest.TestCase):
    def test_turns_of_one_session_never_overlap(self):
        executor, state_lock = IdempotentExecutor(), threading.Lock()
        running, peak = [0], [0]

        def work():
            with state_lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            threading.Event().wait(0.02)
            with state_lock:
                running[0] -= 1
            return "done"

        threads = [threading.Thread(target=executor.run, args=("s", f"k{i}", work)) for i in range(6)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        self.assertEqual(peak[0], 1)

    def test_concurrent_duplicates_run_once_and_the_rest_replay(self):
        executor, calls, results = IdempotentExecutor(), [], []

        def work():
            calls.append(1)
            threading.Event().wait(0.05)
            return "result"

        threads = [threading.Thread(target=lambda: results.append(executor.run("s", "same", work))) for _ in range(5)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(replayed for _, replayed in results), [False, True, True, True, True])
        self.assertTrue(all(value == "result" for value, _ in results))

    def test_different_sessions_do_not_block_each_other(self):
        executor = IdempotentExecutor()
        barrier = threading.Barrier(2, timeout=2)  # both must be inside fn at once, or the barrier breaks
        failures = []

        def work():
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                failures.append(1)
            return "ok"

        threads = [threading.Thread(target=executor.run, args=(f"s{i}", "k", work)) for i in range(2)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
