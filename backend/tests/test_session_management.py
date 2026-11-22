
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.models import TurnHistoryEntry
from backend.storage import SQLiteSessionStore
from engine.models import GameState


class SessionManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.db_path = Path(self.tmp.name) / "sessions.db"
        self.store = SQLiteSessionStore(self.db_path)

        store_patch = patch.object(
            backend,
            "get_session_store",
            return_value=self.store,
        )
        store_patch.start()
        self.addCleanup(store_patch.stop)

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()

        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)

    def create_session(self, name):
        response = self.client.post(
            "/sessions",
            json={"name": name},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def test_list_sessions(self):
        first_id = self.create_session("First campaign")
        second_id = self.create_session("Second campaign")

        response = self.client.get("/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"id": second_id, "name": "Second campaign"},
                {"id": first_id, "name": "First campaign"},
            ],
        )

    def test_list_sessions_after_memory_reset(self):
        session_id = self.create_session("Saved campaign")

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()

        response = self.client.get("/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [{"id": session_id, "name": "Saved campaign"}],
        )

        state_response = self.client.get(
            f"/sessions/{session_id}/state"
        )
        self.assertEqual(state_response.status_code, 200)

    def test_delete_session_and_related_data(self):
        session_id = self.create_session("To delete")

        _, initial_state, _ = self.store.load_session(session_id)

        next_state = GameState(
            session_id=initial_state.session_id,
            revision=1,
            turn_number=1,
            location_id=initial_state.location_id,
            characters=initial_state.characters,
            world_flags=initial_state.world_flags,
        )

        entry = TurnHistoryEntry(
            character_id="hero",
            player_action="Open the door",
            narrative="The door opens.",
            dm_notes="Private notes",
        )

        self.store.save_turn(
            session_id,
            next_state,
            entry,
            action_id="completed-action",
            response={"status": "resolved", "accepted": True},
        )

        self.store.save_pending_turn(
            session_id,
            "pending-action",
            {"action": {"action_id": "pending-action"}},
        )

        response = self.client.delete(f"/sessions/{session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "deleted", "session_id": session_id},
        )

        self.assertIsNone(self.store.load_session(session_id))
        self.assertIsNone(
            self.store.load_processed_turn(
                session_id, "completed-action"
            )
        )
        self.assertIsNone(
            self.store.load_pending_turn(session_id)
        )
        self.assertNotIn(session_id, backend.sessions)
        self.assertNotIn(session_id, backend.game_states)
        self.assertNotIn(session_id, backend.game_histories)

        self.assertEqual(self.client.get("/sessions").json(), [])
        self.assertEqual(
            self.client.get(
                f"/sessions/{session_id}/state"
            ).status_code,
            404,
        )

    def test_delete_unknown_session(self):
        response = self.client.delete("/sessions/unknown")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            "Session not found",
        )

    def test_delete_session_twice(self):
        session_id = self.create_session("Temporary")

        first = self.client.delete(f"/sessions/{session_id}")
        second = self.client.delete(f"/sessions/{session_id}")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)


if __name__ == "__main__":
    unittest.main()
