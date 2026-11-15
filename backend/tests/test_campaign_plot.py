
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from backend.storage import SQLiteSessionStore


class CampaignPlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.db_path = Path(self.tmp.name) / "game.db"
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

    def test_plot_is_persisted(self):
        response = self.client.post(
            "/sessions",
            json={
                "name": "Dark Forest",
                "hidden_plot": "The king is secretly a dragon.",
            },
        )

        self.assertEqual(response.status_code, 200)
        session_id = response.json()["id"]

        restored_store = SQLiteSessionStore(self.db_path)

        self.assertEqual(
            restored_store.load_hidden_plot(session_id),
            "The king is secretly a dragon.",
        )

    def test_plot_is_private(self):
        response = self.client.post(
            "/sessions",
            json={
                "name": "Secret campaign",
                "hidden_plot": "The wizard is the villain.",
            },
        )
        session_id = response.json()["id"]

        self.assertNotIn("hidden_plot", response.json())

        state = self.client.get(
            f"/sessions/{session_id}/state"
        )

        self.assertEqual(state.status_code, 200)
        self.assertNotIn("hidden_plot", state.text)
        self.assertNotIn(
            "The wizard is the villain.",
            state.text,
        )

        listed = self.client.get("/sessions")
        self.assertNotIn("hidden_plot", listed.text)

    def test_different_sessions_have_different_plots(self):
        first = self.client.post(
            "/sessions",
            json={
                "name": "First",
                "hidden_plot": "Plot A",
            },
        ).json()["id"]

        second = self.client.post(
            "/sessions",
            json={
                "name": "Second",
                "hidden_plot": "Plot B",
            },
        ).json()["id"]

        self.assertEqual(
            self.store.load_hidden_plot(first),
            "Plot A",
        )
        self.assertEqual(
            self.store.load_hidden_plot(second),
            "Plot B",
        )

    def test_plot_is_deleted_with_session(self):
        response = self.client.post(
            "/sessions",
            json={
                "name": "Temporary",
                "hidden_plot": "Secret",
            },
        )
        session_id = response.json()["id"]

        deleted = self.client.delete(
            f"/sessions/{session_id}"
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(
            self.store.load_hidden_plot(session_id)
        )

    def test_existing_database_is_migrated(self):
        import sqlite3

        old_db = Path(self.tmp.name) / "old.db"

        with sqlite3.connect(old_db) as connection:
            connection.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL)"
            )

        migrated = SQLiteSessionStore(old_db)

        with migrated._connect() as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            }

        self.assertIn("hidden_plot", columns)


if __name__ == "__main__":
    unittest.main()
