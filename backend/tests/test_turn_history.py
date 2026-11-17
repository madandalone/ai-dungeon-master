
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import (
    app,
    game_histories,
    game_states,
    sessions,
    get_orchestrator,
    FakeDungeonMaster,
    FakeRulesProvider,
)
import backend.main as backend
from backend.storage import SQLiteSessionStore
from backend.tests.helpers import create_hero
from integration.factory import build_orchestrator
class TestTurnHistory(unittest.TestCase):

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "game.db")
        self.store_patch = patch.object(backend, "get_session_store", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        sessions.clear()
        game_states.clear()
        game_histories.clear()

        self.client = TestClient(app)
        response = self.client.post(
            "/sessions",
            json={"name": "Test Campaign"},
        )
        self.assertEqual(response.status_code, 200)
        self.session_id = response.json()["id"]
        self.character_id = create_hero(self.client, self.session_id)["id"]

        app.dependency_overrides[get_orchestrator] = lambda: build_orchestrator(
            dm=FakeDungeonMaster(),
            rules=FakeRulesProvider(),
        )
        self.addCleanup(app.dependency_overrides.clear)

    def send_turn(self, character_id=None, text="Я открываю дверь"):
        return self.client.post(
            f"/sessions/{self.session_id}/turns",
            json={
                "character_id": character_id or self.character_id,
                "text": text,
            },
        )

    def test_two_turns_saved_in_history(self):
        first = self.send_turn(text="Я открываю дверь")
        second = self.send_turn(text="Я осматриваю комнату")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["accepted"])
        self.assertTrue(second.json()["accepted"])

        history = game_histories[self.session_id]

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].player_action, "Я открываю дверь")
        self.assertEqual(history[1].player_action, "Я осматриваю комнату")
        self.assertEqual(second.json()["revision"], 3)

    def test_rejected_turn_not_saved(self):
        response = self.send_turn(character_id="unknown")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["accepted"])
        self.assertEqual(len(game_histories[self.session_id]), 0)
        self.assertEqual(game_states[self.session_id].revision, 1)

    def test_dm_notes_not_exposed(self):
        response = self.send_turn()

        self.assertTrue(response.json()["accepted"])
        self.assertEqual(
            game_histories[self.session_id][0].dm_notes,
            "Test turn processed.",
        )
        self.assertNotIn("dm_notes", response.json())
        self.assertNotIn("Test turn processed.", response.text)


if __name__ == "__main__":
    unittest.main()
