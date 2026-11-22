"""End-to-end game scenario through the real FastAPI app, SQLite, Rules Service, orchestrator and Engine.

Only the LLM is replaced by a deterministic stand-in (`ScriptedDM`), so the scenario is reproducible and free.
`RealLlmScenario` runs the same flow against a real provider; it is skipped unless E2E_REAL_LLM=1 and
OPENAI_API_KEY are set (see README, "End-to-end with a real model").
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.models import (
    CampaignStartResponse,
    CheckResolution,
    SkillCheckProposal,
    StateDelta,
    TurnPlan,
    TurnResponse,
)
from backend.storage import SQLiteSessionStore
from integration import RulesSettings, build_orchestrator
from integration.factory import build_rules_provider

PLOT = "the abbot feeds the village to the crypt"
SECRET_NOTE = "the abbot watched the hero cross the gap"
BOOK = """# Jumping
Your Strength score determines how far you can jump. A long jump covers feet equal to your Strength score.

# Falling
A fall from a great height ends in injury: you take 1d6 bludgeoning damage for every 10 feet you fall.
"""
ABILITIES = {"strength": 14, "dexterity": 12, "constitution": 13, "intelligence": 10, "wisdom": 11, "charisma": 8}


class ScriptedDM:
    """Writes a campaign, asks for a Strength check on a risky jump, then narrates whatever the Engine rolled."""

    def __init__(self):
        self.campaigns, self.plans, self.resolutions = [], [], []

    def generate_campaign_start(self, request):
        self.campaigns.append(request)
        return CampaignStartResponse(
            campaign_title="Склеп некроманта", hidden_plot=PLOT, starting_location="Затопленный склеп",
            room_context="Вода по колено, у стены зияет провал.", intro_narrative="Вы входите в затопленный склеп.",
        )

    def plan_turn(self, request):
        self.plans.append(request)
        if "перепрыг" in request.player_action:
            return TurnPlan(
                status="check",
                check=SkillCheckProposal(ability="strength", difficulty=10, reason="Прыжок через провал"),
            )
        return TurnPlan(status="resolved", response=self._response("Вы осматриваетесь.", 0, request))

    def resolve_check(self, request, checks, *, allow_next):
        self.resolutions.append((request, list(checks)))
        won = all(check.success for check in checks)
        text = "Вы перепрыгиваете провал." if won else "Вы срываетесь и ударяетесь о камни."
        response = self._response(text, 0 if won else -2, request)
        return CheckResolution(status="resolved", outcome="success" if won else "failure", response=response)

    @staticmethod
    def _response(text, hp_change, request):
        updates = [StateDelta(character_name=request.acting_character, hp_change=hp_change)] if hp_change else []
        return TurnResponse(narrative=text, state_updates=updates, new_location=None, dm_thoughts=SECRET_NOTE)


class Scenario(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root / "rules.md"
        self.book.write_text(BOOK, encoding="utf-8")
        self.db_path = self.root / "game.db"
        self.addCleanup(backend.app.dependency_overrides.clear)
        self.boot()

    def boot(self):
        """Start the application: empty memory, SQLite opened from disk, orchestrator built from settings."""
        store = SQLiteSessionStore(self.db_path)
        patcher = patch.object(backend, "get_session_store", return_value=store)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()
        self.store = store
        self.dm = self.make_dm()
        self.orchestrator = build_orchestrator(
            dm=self.dm, rules=build_rules_provider(RulesSettings(self.book, "keyword")),
        )
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: self.orchestrator
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)

    def make_dm(self):
        return ScriptedDM()

    def restart(self):
        self.client.close()
        self.boot()

    def create_hero(self, session_id, revision):
        response = self.client.post(f"/sessions/{session_id}/characters", json={
            "expected_revision": revision, "name": "Элиас", "concept": "Плут", "max_hp": 12,
            "stats": ABILITIES, "starting_item": "Кинжал",
        })
        self.assertEqual(response.status_code, 200, response.text)
        hero = next(c for c in response.json()["characters"] if c["name"] == "Элиас")
        return hero["id"], response.json()["revision"]


class FullGameScenarioTests(Scenario):
    def test_create_play_roll_restart_and_continue(self):
        # 1. a session and a hero
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        session_id = self.client.post("/sessions", json={"name": "Склеп"}).json()["id"]
        hero_id, revision = self.create_hero(session_id, 0)
        self.assertEqual(revision, 1)

        # 2. the first action: the campaign is written, rules are fetched, the DM asks for a check
        text = "Я хочу перепрыгнуть через пропасть"
        first = self.client.post(f"/sessions/{session_id}/turns", json={
            "action_id": "a-1", "expected_revision": revision, "character_id": hero_id, "text": text,
        })
        self.assertEqual(first.status_code, 202, first.text)
        pending = first.json()
        self.assertEqual(pending["status"], "awaiting_roll")
        self.assertEqual(len(self.dm.campaigns), 1, "the campaign is written once, before the first turn")
        self.assertEqual({c.name for c in self.dm.campaigns[0].characters}, {"Элиас"})
        planned = self.dm.plans[0]
        self.assertEqual((planned.hidden_plot, planned.acting_character), (PLOT, "Элиас"))
        self.assertEqual(planned.game_state.location, "Затопленный склеп")
        self.assertIn("long jump", planned.rules_summary, "a Russian action reached the English rule via the glossary")

        # 3. the d20 is rolled by the Engine and the DM narrates the outcome
        rolled = self.client.post(
            f"/sessions/{session_id}/turns/a-1/roll",
            json={"sequence": pending["pending_turn"]["sequence"]},
        )
        self.assertEqual(rolled.status_code, 200, rolled.text)
        done = rolled.json()
        self.assertEqual((done["status"], done["accepted"], done["warnings"]), ("resolved", True, []))
        check = self.dm.resolutions[0][1][0]
        self.assertTrue(1 <= check.roll <= 20)
        self.assertEqual(check.total, check.roll + check.modifier)
        self.assertEqual(check.modifier, 2, "Strength 14 gives +2")
        self.assertEqual(check.success, check.total >= check.difficulty)
        hp = 12 if check.success else 10
        hero = next(c for c in done["state"]["characters"] if c["id"] == hero_id)
        self.assertEqual(hero["hp"], hp)
        self.assertEqual(done["revision"], 3, "hero + campaign start + one turn")

        # 4. nothing private ever left the server
        everything = first.text + rolled.text + self.client.get(f"/sessions/{session_id}/state").text
        for secret in (PLOT, SECRET_NOTE):
            self.assertNotIn(secret, everything)

        # 5. restart: memory is gone, the database remains
        self.restart()
        restored = self.client.get(f"/sessions/{session_id}/state").json()
        self.assertEqual((restored["revision"], restored["location"]), (3, "Затопленный склеп"))
        self.assertEqual(next(c for c in restored["characters"] if c["id"] == hero_id)["hp"], hp)
        self.assertEqual([entry["kind"] for entry in restored["log"]], ["master", "player", "master"])
        self.assertIsNone(restored["pending_turn"])

        # 6. a retry of the finished turn is answered from the database, the DM is not asked again
        replay = self.client.post(f"/sessions/{session_id}/turns", json={
            "action_id": "a-1", "expected_revision": revision, "character_id": hero_id, "text": text,
        })
        self.assertEqual((replay.status_code, replay.json()["revision"]), (200, 3))
        self.assertEqual((self.dm.plans, self.dm.campaigns), ([], []))

        # 7. the game goes on with the stored plot
        second = self.client.post(f"/sessions/{session_id}/turns", json={
            "action_id": "a-2", "expected_revision": 3, "character_id": hero_id, "text": "Я осматриваю комнату",
        })
        self.assertEqual((second.status_code, second.json()["accepted"], second.json()["revision"]), (200, True, 4))
        self.assertEqual(self.dm.campaigns, [], "no new campaign after the restart")
        self.assertEqual(self.dm.plans[0].hidden_plot, PLOT)
        self.assertEqual(len(self.dm.plans[0].history), 2, "intro and the first turn are remembered")

    def test_a_rules_outage_does_not_stop_the_game_and_is_reported(self):
        from rules_service import RulesUnavailableError

        class DownRules:
            def get_rules(self, query):
                raise RulesUnavailableError("embedding model is not available")

        self.orchestrator = build_orchestrator(dm=self.dm, rules=DownRules())
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: self.orchestrator
        session_id = self.client.post("/sessions", json={"name": "Склеп"}).json()["id"]
        hero_id, revision = self.create_hero(session_id, 0)
        with self.assertLogs("integration.orchestrator", "WARNING"):
            turn = self.client.post(f"/sessions/{session_id}/turns", json={
                "character_id": hero_id, "text": "Я осматриваю комнату", "expected_revision": revision,
            })
        self.assertEqual((turn.status_code, turn.json()["accepted"]), (200, True))
        self.assertEqual(turn.json()["warnings"], [{"code": "RULES_UNAVAILABLE"}])
        self.assertIn("No specific rules", self.dm.plans[0].rules_summary)

    def test_a_question_without_matching_rules_still_gets_an_answer(self):
        session_id = self.client.post("/sessions", json={"name": "Склеп"}).json()["id"]
        hero_id, revision = self.create_hero(session_id, 0)
        turn = self.client.post(f"/sessions/{session_id}/turns", json={
            "character_id": hero_id, "text": "Здравствуйте, трактирщик", "expected_revision": revision,
        })
        self.assertTrue(turn.json()["accepted"])
        self.assertIn("No specific rules", self.dm.plans[0].rules_summary)
        self.assertEqual(turn.json()["warnings"], [])

    def test_session_management_end_to_end(self):
        first = self.client.post("/sessions", json={"name": "Первая"}).json()["id"]
        second = self.client.post("/sessions", json={"name": "Вторая"}).json()["id"]
        self.assertEqual({s["name"] for s in self.client.get("/sessions").json()}, {"Первая", "Вторая"})

        self.restart()
        self.assertEqual({s["id"] for s in self.client.get("/sessions").json()}, {first, second})
        self.assertEqual(self.client.get(f"/sessions/{first}/state").status_code, 200)

        self.assertEqual(self.client.delete(f"/sessions/{first}").json()["status"], "deleted")
        self.assertEqual(self.client.get(f"/sessions/{first}/state").status_code, 404)
        self.assertEqual({s["id"] for s in self.client.get("/sessions").json()}, {second})
        self.assertEqual(self.client.delete(f"/sessions/{first}").status_code, 404)


@unittest.skipUnless(
    os.getenv("E2E_REAL_LLM") == "1" and os.getenv("OPENAI_API_KEY"),
    "set E2E_REAL_LLM=1 and OPENAI_API_KEY to run against a real provider",
)
class RealLlmScenario(Scenario):
    """Same flow with the real DM. The model's words are not asserted, only the invariants of the game."""

    def make_dm(self):
        from integration import build_dungeon_master

        return build_dungeon_master()

    def test_a_real_model_plays_a_turn_and_the_game_survives_a_restart(self):
        session_id = self.client.post("/sessions", json={"name": "E2E"}).json()["id"]
        hero_id, revision = self.create_hero(session_id, 0)
        response = self.client.post(f"/sessions/{session_id}/turns", json={
            "action_id": "real-1", "expected_revision": revision, "character_id": hero_id,
            "text": "Я осторожно осматриваю зал и прислушиваюсь",
        })
        self.assertIn(response.status_code, (200, 202), response.text)
        rolls = 0
        while response.status_code == 202 and rolls < 3:
            rolls += 1
            response = self.client.post(
                f"/sessions/{session_id}/turns/real-1/roll",
                json={"sequence": response.json()["pending_turn"]["sequence"]},
            )
            self.assertIn(response.status_code, (200, 202), response.text)
        body = response.json()
        self.assertEqual((body["status"], body["accepted"]), ("resolved", True))
        self.assertTrue(body["narration"].strip())
        print("\nE2E real model:", json.dumps({
            "rolls": rolls, "revision": body["revision"], "narration_chars": len(body["narration"]),
            "events": [event.get("type") for event in body.get("events", [])],
        }, ensure_ascii=False))

        record = self.store.load_campaign(session_id)
        self.assertTrue(record["started"] and record["hidden_plot"].strip())
        self.assertNotIn(record["hidden_plot"], response.text)
        self.restart()
        restored = self.client.get(f"/sessions/{session_id}/state").json()
        self.assertEqual(restored["revision"], body["revision"])
        self.assertGreaterEqual(len(restored["log"]), 3)


if __name__ == "__main__":
    unittest.main()
