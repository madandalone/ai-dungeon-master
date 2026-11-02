import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from backend.storage import SQLiteSessionStore
from integration.factory import build_orchestrator


class TestPersistenceAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.store = SQLiteSessionStore(
            Path(self.tmp.name) / "game.db"
        )

        self.store_patch = patch.object(
            backend, "get_session_store", return_value=self.store
        )
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()

        orchestrator = build_orchestrator(
            dm=backend.FakeDungeonMaster(),
            rules=backend.FakeRulesProvider(),
        )
        backend.app.dependency_overrides[backend.get_orchestrator] = (
            lambda: orchestrator
        )
        self.addCleanup(backend.app.dependency_overrides.clear)

        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
    def test_duplicate_turn_not_applied_twice(self):
        response = self.client.post(
            "/sessions", json={"name": "Duplicate Test"}
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]

        payload = {
            "character_id": "hero",
            "text": "Я открываю дверь",
            "action_id": "duplicate-action-1",
            "expected_revision": 0,
        }

        first = self.client.post(
            f"/sessions/{session_id}/turns", json=payload
        )
        second = self.client.post(
            f"/sessions/{session_id}/turns", json=payload
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["accepted"])
        self.assertEqual(first.json(), second.json())

        restored = self.store.load_session(session_id)
        self.assertIsNotNone(restored)
        _, state, history = restored

        self.assertEqual(state.revision, 1)
        self.assertEqual(state.characters[0].hp, 9)
        self.assertEqual(len(history), 1)
    def test_turn_survives_restart(self):
        response = self.client.post(
            "/sessions", json={"name": "Persistence Test"}
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]

        response = self.client.post(
            f"/sessions/{session_id}/turns",
            json={"character_id": "hero", "text": "Я открываю дверь"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepted"])

        # Имитируем перезапуск Backend.
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()

        response = self.client.get(
            f"/sessions/{session_id}/state"
        )
        self.assertEqual(response.status_code, 200)
        public_log = response.json()["log"]
        self.assertEqual(
            [entry["kind"] for entry in public_log],
            ["player", "master"],
        )
        self.assertEqual(public_log[0]["text"], "Я открываю дверь")
        self.assertNotIn("dm_notes", response.text)

        restored = self.store.load_session(session_id)
        self.assertIsNotNone(restored)

        _, state, history = restored
        self.assertEqual(state.revision, 1)
        self.assertEqual(state.characters[0].hp, 9)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0].player_action, "Я открываю дверь"
        )
    def test_concurrent_actions(self):
        from concurrent.futures import ThreadPoolExecutor

        response = self.client.post(
            "/sessions", json={"name": "Concurrent Test"}
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]

        def send_action(index):
            return self.client.post(
                f"/sessions/{session_id}/actions",
                json={
                    "character_id": "hero",
                    "text": f"Action {index}",
                    "health_delta": -1,
                },
            )

        with ThreadPoolExecutor(max_workers=5) as executor:
            responses = list(executor.map(send_action, range(5)))

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["accepted"])

        restored = self.store.load_session(session_id)
        self.assertIsNotNone(restored)

        _, state, _ = restored
        self.assertEqual(state.revision, 5)
        self.assertEqual(state.characters[0].hp, 5)

if __name__ == "__main__":
    unittest.main()
