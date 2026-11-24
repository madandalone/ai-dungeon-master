"""A real AIDungeonMaster with a scripted model client behind the real HTTP routes.

Reproduces the field failure (a small local model answering a risky Russian action with unusable JSON)
and pins what must hold: a controlled 502, no change to the session, and an immediate retry that works.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.dm_engine import AIDungeonMaster
from backend.storage import SQLiteSessionStore
from integration.factory import build_orchestrator

ABILITIES = {"strength": 14, "dexterity": 12, "constitution": 13, "intelligence": 10, "wisdom": 12, "charisma": 10}
ACTION = "Пытаюсь перепрыгнуть широкую пропасть по скользкому краю."
RESOLVED = {"status": "resolved", "response": {
    "narrative": "Вы осматриваете комнату.", "state_updates": [], "new_location": None, "dm_thoughts": "",
    "requires_check": False, "check": None,
}}
INCOMPLETE = {"status": "check"}
CHECK = {"status": "check", "check": {"ability": "dexterity", "difficulty": 15, "reason": "Скользкий край."}}


def completion(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), finish_reason="stop",
    )])


class Rules:
    def get_rules(self, query):
        return "rule"


class ScriptedModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "game.db")
        patcher = patch.object(backend, "get_session_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        self.addCleanup(backend.app.dependency_overrides.clear)
        # An explicit plot keeps the campaign writer out of this scenario: only the turn is scripted.
        self.sid = self.client.post("/sessions", json={"name": "Демо", "hidden_plot": "Пропасть."}).json()["id"]
        created = self.client.post(f"/sessions/{self.sid}/characters", json={
            "expected_revision": 0, "name": "Арагорн", "concept": "Следопыт", "max_hp": 10,
            "stats": ABILITIES, "starting_item": "Длинный меч",
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.hero_id = next(c["id"] for c in created.json()["characters"] if c["name"] == "Арагорн")
        self.revision = created.json()["revision"]

    def model(self, *answers):
        scripted = Mock()
        scripted.chat.completions.create.side_effect = [completion(a) for a in answers]
        orchestrator = build_orchestrator(dm=AIDungeonMaster(scripted), rules=Rules())
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: orchestrator
        return scripted

    def turn(self, action_id="a-1", text=ACTION):
        return self.client.post(
            f"/sessions/{self.sid}/turns",
            json={"character_id": self.hero_id, "text": text, "action_id": action_id, "expected_revision": self.revision},
        )

    def test_unusable_model_answers_give_a_controlled_502_and_leave_the_session_untouched(self):
        scripted = self.model(RESOLVED, INCOMPLETE)
        with self.assertLogs("ai_dm.dm_engine", "WARNING") as logs:
            response = self.turn()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Dungeon Master returned an invalid response after one repair attempt.")
        self.assertEqual(scripted.chat.completions.create.call_count, 2)
        self.assertEqual(len(logs.output), 2, "the first rejection and the failed repair are both recorded")
        state = self.client.get(f"/sessions/{self.sid}/state").json()
        self.assertEqual((state["revision"], state["turn_number"], state["log"], state["pending_turn"]), (1, 0, [], None))
        self.assertEqual(self.store.load_session(self.sid)[2], [])

    def test_the_same_action_can_be_retried_right_after_the_failure(self):
        self.model(RESOLVED, INCOMPLETE)
        with self.assertLogs("ai_dm.dm_engine", "WARNING"):
            self.assertEqual(self.turn().status_code, 502)

        self.model(CHECK)
        retry = self.turn()
        self.assertEqual(retry.status_code, 202)
        pending = retry.json()["pending_turn"]
        self.assertEqual(
            (pending["ability"], pending["difficulty"], pending["modifier"], pending["reason"]),
            ("dexterity", 15, 1, "Скользкий край."),
        )

    def test_a_risky_action_waits_for_the_engine_roll_after_one_request(self):
        scripted = self.model(CHECK)
        response = self.turn()
        self.assertEqual((response.status_code, response.json()["status"]), (202, "awaiting_roll"))
        self.assertEqual(scripted.chat.completions.create.call_count, 1)
        schema = scripted.chat.completions.create.call_args.kwargs["response_format"]["json_schema"]["name"]
        self.assertEqual(schema, "TurnPlan")
        state = self.client.get(f"/sessions/{self.sid}/state").json()
        self.assertEqual((state["revision"], state["log"]), (1, []), "nothing changes before the roll")


if __name__ == "__main__":
    unittest.main()
