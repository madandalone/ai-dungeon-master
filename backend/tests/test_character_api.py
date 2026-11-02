import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from backend.storage import SQLiteSessionStore


class CharacterAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "game.db")
        self.store_patch = patch.object(
            backend,
            "get_session_store",
            return_value=self.store,
        )
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)

        response = self.client.post("/sessions", json={"name": "Город теней"})
        self.assertEqual(response.status_code, 200)
        self.session_id = response.json()["id"]

    def payload(self, **changes):
        data = {
            "expected_revision": 0,
            "name": "Элиас",
            "concept": "Плут",
            "max_hp": 12,
            "stats": {"ловкость": 3},
            "starting_item": "кинжал",
        }
        data.update(changes)
        return data

    def test_character_is_created_and_persisted(self):
        response = self.client.post(
            f"/sessions/{self.session_id}/characters",
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["revision"], 1)
        self.assertEqual(body["turn_number"], 0)
        self.assertEqual(len(body["characters"]), 2)
        created = body["characters"][1]
        self.assertEqual(created["name"], "Элиас")
        self.assertEqual(created["inventory"][0]["name"], "кинжал")

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()

        restored = self.client.get(f"/sessions/{self.session_id}/state")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["characters"][1]["name"], "Элиас")

    def test_stale_revision_does_not_create_character(self):
        first = self.client.post(
            f"/sessions/{self.session_id}/characters",
            json=self.payload(),
        )
        self.assertEqual(first.status_code, 200)

        stale = self.client.post(
            f"/sessions/{self.session_id}/characters",
            json=self.payload(name="Мира"),
        )

        self.assertEqual(stale.status_code, 409)
        state = self.client.get(f"/sessions/{self.session_id}/state").json()
        self.assertEqual(len(state["characters"]), 2)

    def test_invalid_character_does_not_change_state(self):
        response = self.client.post(
            f"/sessions/{self.session_id}/characters",
            json=self.payload(name="   "),
        )

        self.assertEqual(response.status_code, 422)
        state = self.client.get(f"/sessions/{self.session_id}/state").json()
        self.assertEqual(state["revision"], 0)
        self.assertEqual(len(state["characters"]), 1)

    def test_unknown_session_returns_not_found(self):
        response = self.client.post(
            "/sessions/missing/characters",
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
