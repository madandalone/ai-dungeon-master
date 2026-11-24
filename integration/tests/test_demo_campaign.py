"""The demo campaign from docs/demo_campaign.json played through the real routes with a scripted model.

The model client answers by schema, and for the second stage reads the outcome the Engine's roll demands from
the prompt, so the scenario holds whatever the d20 shows.
"""

import json
import re
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

DEMO = json.loads((Path(__file__).resolve().parents[2] / "docs" / "demo_campaign.json").read_text(encoding="utf-8"))
SAFE_NARRATION = "Вы осматриваете сундук: крышка заклинила, а замок простой."
WON = "Крышка поддаётся рывку, и внутри виден медный ключ."
LOST = "Крышка остаётся закрытой, но можно попробовать другой подход."


def completion(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), finish_reason="stop",
    )])


def turn_payload(narrative):
    return {"narrative": narrative, "state_updates": [], "new_location": None, "dm_thoughts": "",
            "requires_check": False, "check": None}


class ScriptedModel:
    """Plays the Dungeon Master of the demo: a safe action, then a Strength check at DC 10."""

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        schema = kwargs["response_format"]["json_schema"]["name"]
        prompt = " ".join(message["content"] for message in kwargs["messages"])
        self.calls.append((schema, prompt))
        if schema == "CheckDecision":
            if DEMO["actions"]["risky"] in prompt:
                return completion({"needs_check": True, "ability": "strength", "difficulty": 10,
                                   "reason": "Крышка старого сундука заклинила, её нужно открыть усилием"})
            return completion({"needs_check": False, "ability": None, "difficulty": None, "reason": ""})
        if schema == "TurnResponse":
            return completion(turn_payload(SAFE_NARRATION))
        outcome = re.search(r"outcome: (success|failure)", prompt).group(1)
        return completion({"status": "resolved", "outcome": outcome, "check": None,
                           "response": turn_payload(WON if outcome == "success" else LOST)})


class Rules:
    def get_rules(self, query):
        return "Проверка характеристики: d20 + модификатор против сложности."


class DemoCampaignTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = SQLiteSessionStore(Path(tmp.name) / "demo.db")
        patcher = patch.object(backend, "get_session_store", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()
        self.model = ScriptedModel()
        client = Mock()
        client.chat.completions.create.side_effect = self.model.create
        orchestrator = build_orchestrator(dm=AIDungeonMaster(client), rules=Rules())
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: orchestrator
        self.addCleanup(backend.app.dependency_overrides.clear)
        self.http = TestClient(backend.app)
        self.addCleanup(self.http.close)

    def test_the_demo_runs_from_the_documented_json(self):
        http = self.http
        sid = http.post("/sessions", json={"name": DEMO["session_name"]}).json()["id"]
        created = http.post(f"/sessions/{sid}/characters", json={"expected_revision": 0, **DEMO["hero"]})
        self.assertEqual(created.status_code, 200, created.text)
        hero = created.json()["characters"][0]
        started = http.post(f"/sessions/{sid}/campaign", json={"authored": DEMO["campaign"]})
        self.assertEqual((started.status_code, started.json()["state"]["location"]), (200, "Сторожка"))

        safe = http.post(f"/sessions/{sid}/turns", json={
            "action_id": "safe-1", "character_id": hero["id"], "text": DEMO["actions"]["safe"]})
        self.assertEqual((safe.status_code, safe.json()["status"], safe.json()["accepted"]), (200, "resolved", True))
        self.assertEqual(safe.json()["narration"], SAFE_NARRATION)
        self.assertEqual([schema for schema, _ in self.model.calls], ["CheckDecision", "TurnResponse"])
        self.assertIsNone(safe.json().get("check_outcome"), "a safe action rolls nothing")

        risky = http.post(f"/sessions/{sid}/turns", json={
            "action_id": "risky-1", "character_id": hero["id"], "text": DEMO["actions"]["risky"]})
        self.assertEqual((risky.status_code, risky.json()["status"]), (202, "awaiting_roll"))
        pending = risky.json()["pending_turn"]
        self.assertEqual((pending["ability"], pending["difficulty"], pending["modifier"], pending["score"]),
                         ("strength", 10, 2, 14))
        self.assertEqual(self.model.calls[-1][0], "CheckDecision", "the model only chose the check, no outcome yet")

        rolled = http.post(f"/sessions/{sid}/turns/risky-1/roll", json={"sequence": 1})
        self.assertEqual((rolled.status_code, rolled.json()["status"], rolled.json()["accepted"]),
                         (200, "resolved", True))
        body = rolled.json()
        check = next(event for event in body["events"] if event.get("type") == "skill_check_resolved")
        self.assertTrue(1 <= check["roll"] <= 20)
        self.assertEqual((check["modifier"], check["difficulty"], check["total"]), (2, 10, check["roll"] + 2))
        self.assertEqual(check["success"], check["total"] >= 10)
        self.assertEqual(body["check_outcome"], "success" if check["success"] else "failure")
        self.assertEqual(body["narration"], WON if check["success"] else LOST)
        self.assertNotIn("Ключ", json.dumps(body["state"]["characters"], ensure_ascii=False),
                         "no item until a separate action")

        state = http.get(f"/sessions/{sid}/state").json()
        self.assertEqual(state["revision"], 4, "hero, campaign start and two turns, each counted once")
        self.assertEqual([entry["kind"] for entry in state["log"]], ["master", "player", "master", "player", "master"])
        self.assertEqual(state["characters"][0]["hp"], 10, "a failed check costs nothing")
        self.assertIsNone(state["pending_turn"])

        plot = DEMO["campaign"]["hidden_plot"]
        for schema, prompt in self.model.calls:
            if schema == "CheckDecision":
                self.assertNotIn(plot, prompt, "the small decision never sees the plot")
            else:
                self.assertIn(plot, prompt)
                self.assertIn("Одна комната: деревянный стол", prompt)
        public = safe.text + risky.text + rolled.text + json.dumps(state, ensure_ascii=False)
        self.assertNotIn(plot, public)


if __name__ == "__main__":
    unittest.main()
