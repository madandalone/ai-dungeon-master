import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_dm.models import TurnHistoryEntry
from backend.storage import SQLiteSessionStore, StateRevisionConflict
from engine.models import Character, GameState


def make_state(revision=0, hp=10):
    return GameState(
        session_id="test",
        revision=revision,
        turn_number=revision,
        location_id="forest",
        characters=(
            Character(
                id="hero",
                name="Hero",
                concept="Adventurer",
                hp=hp,
                max_hp=10,
                stats={"strength": 2},
                inventory=(),
            ),
        ),
        world_flags={"door_open": True},
    )


class TestSQLiteSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "game.db"
        self.store = SQLiteSessionStore(self.path)
        self.store.save_session("test", "Campaign", make_state())

    def test_restore_session(self):
        restored = SQLiteSessionStore(self.path).load_session("test")

        self.assertIsNotNone(restored)
        name, state, history = restored

        self.assertEqual(name, "Campaign")
        self.assertEqual(state.characters[0].hp, 10)
        self.assertTrue(state.world_flags["door_open"])
        self.assertEqual(history, [])

    def test_save_and_restore_turn(self):
        entry = TurnHistoryEntry(
            player_action="Атакую гоблина",
            narrative="Гоблин ранен",
            dm_notes="Успешная атака",
        )

        self.store.save_turn("test", make_state(1, 8), entry)

        _, state, history = SQLiteSessionStore(
            self.path
        ).load_session("test")

        self.assertEqual(state.revision, 1)
        self.assertEqual(state.characters[0].hp, 8)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0], entry)

    def test_reject_stale_revision(self):
        entry = TurnHistoryEntry(
            player_action="Атакую",
            narrative="Атака",
            dm_notes="",
        )

        self.store.save_turn("test", make_state(1, 8), entry)

        with self.assertRaises(StateRevisionConflict):
            self.store.save_turn("test", make_state(1, 5), entry)

        _, state, history = self.store.load_session("test")

        self.assertEqual(state.revision, 1)
        self.assertEqual(state.characters[0].hp, 8)
        self.assertEqual(len(history), 1)

    def test_unknown_session(self):
        self.assertIsNone(self.store.load_session("unknown"))

    def test_rollback_on_history_error(self):
        entry = TurnHistoryEntry(
            player_action="Атакую",
            narrative="Атака",
            dm_notes="",
        )

        with self.store._connect() as connection:
            connection.execute("""
                CREATE TRIGGER fail_history_insert
                BEFORE INSERT ON game_histories
                BEGIN
                    SELECT RAISE(ABORT, 'simulated failure');
                END;
            """)

        with self.assertRaises(Exception):
            self.store.save_turn("test", make_state(1, 8), entry)

        _, state, history = self.store.load_session("test")
        self.assertEqual(state.revision, 0)
        self.assertEqual(state.characters[0].hp, 10)
        self.assertEqual(history, [])


if __name__ == "__main__":
    unittest.main()
