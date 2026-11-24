"""Permanent acceptance test for the six-step classroom demo scenario."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.models import CheckResolution, SkillCheckProposal, StateDelta, TurnPlan, TurnResponse
from backend.storage import SQLiteSessionStore
from engine.dice import SequenceSource
from integration.factory import build_orchestrator


class PartyDungeonMaster:
    def plan_turn(self, request):
        if "толкает" in request.player_action.casefold():
            return TurnPlan(
                status="check",
                check=SkillCheckProposal(
                    ability="strength",
                    difficulty=15,
                    reason="Мира пытается столкнуть Элиаса с устойчивой позиции.",
                ),
            )
        return TurnPlan(
            status="resolved",
            response=TurnResponse(
                narrative="Элиас внимательно осматривает зал.",
                state_updates=[],
                new_location=None,
                dm_thoughts="",
            ),
        )

    def resolve_check(self, request, checks, *, allow_next):
        check = checks[-1]
        return CheckResolution(
            status="resolved",
            outcome="success" if check.success else "failure",
            response=TurnResponse(
                narrative=(
                    "Мира толкает Элиаса, и тот теряет равновесие."
                    if check.success else "Элиас удерживается на ногах."
                ),
                state_updates=[StateDelta(character_name="Элиас", hp_change=-1)] if check.success else [],
                new_location=None,
                dm_thoughts="",
            ),
        )


class PartyAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "acceptance.db")
        store_patch = patch.object(backend, "get_session_store", return_value=self.store)
        store_patch.start()
        self.addCleanup(store_patch.stop)
        random_patch = patch.object(
            backend,
            "SystemRandomSource",
            side_effect=lambda: SequenceSource([18]),
        )
        random_patch.start()
        self.addCleanup(random_patch.stop)
        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()
        orchestrator = build_orchestrator(dm=PartyDungeonMaster(), rules=backend.FakeRulesProvider())
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: orchestrator
        self.addCleanup(backend.app.dependency_overrides.clear)
        self.http = TestClient(backend.app)
        self.addCleanup(self.http.close)

    @staticmethod
    def hero(name, strength):
        return {
            "name": name,
            "concept": "Искатель приключений",
            "max_hp": 10,
            "stats": {
                "strength": strength,
                "dexterity": 10,
                "constitution": 10,
                "intelligence": 10,
                "wisdom": 10,
                "charisma": 10,
            },
            "starting_item": "",
        }

    def test_create_two_heroes_play_safe_and_risky_turn_then_delete(self):
        created = self.http.post("/sessions", json={"name": "Демонстрация"})
        self.assertEqual(created.status_code, 200)
        session_id = created.json()["id"]

        first = self.http.post(
            f"/sessions/{session_id}/characters",
            json={"expected_revision": 0, **self.hero("Элиас", 10)},
        )
        second = self.http.post(
            f"/sessions/{session_id}/characters",
            json={"expected_revision": 1, **self.hero("Мира", 14)},
        )
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        elias, mira = second.json()["characters"]

        safe = self.http.post(
            f"/sessions/{session_id}/turns",
            json={
                "action_id": "safe-turn",
                "expected_revision": 2,
                "character_id": elias["id"],
                "text": "Элиас осматривает зал.",
            },
        )
        self.assertEqual((safe.status_code, safe.json()["accepted"]), (200, True))

        risky = self.http.post(
            f"/sessions/{session_id}/turns",
            json={
                "action_id": "risky-turn",
                "expected_revision": safe.json()["revision"],
                "character_id": mira["id"],
                "text": "Мира толкает Элиаса с края площадки.",
            },
        )
        self.assertEqual((risky.status_code, risky.json()["status"]), (202, "awaiting_roll"))
        pending = risky.json()["pending_turn"]
        self.assertEqual((pending["character_id"], pending["ability"], pending["modifier"]), (mira["id"], "strength", 2))

        resolved = self.http.post(
            f"/sessions/{session_id}/turns/risky-turn/roll",
            json={"sequence": 1},
        )
        self.assertEqual((resolved.status_code, resolved.json()["check_outcome"]), (200, "success"))
        check = next(event for event in resolved.json()["events"] if event["type"] == "skill_check_resolved")
        self.assertEqual((check["roll"], check["modifier"], check["total"]), (18, 2, 20))
        heroes = {hero["name"]: hero for hero in resolved.json()["state"]["characters"]}
        self.assertEqual((heroes["Элиас"]["hp"], heroes["Мира"]["hp"]), (9, 10))

        deleted = self.http.delete(f"/sessions/{session_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.http.get(f"/sessions/{session_id}/state").status_code, 404)


if __name__ == "__main__":
    unittest.main()
