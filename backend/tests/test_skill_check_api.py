import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as backend
from ai_dm.models import (
    CheckResolution,
    SkillCheckProposal,
    StateDelta,
    TurnPlan,
    TurnResponse,
)
from backend.storage import SQLiteSessionStore
from engine.dice import SequenceSource
from integration.factory import build_orchestrator


class CheckDungeonMaster:
    def plan_turn(self, request):
        return TurnPlan(
            status="check",
            check=SkillCheckProposal(
                ability="strength", difficulty=10, reason="Дверь заклинило.",
            ),
        )

    def resolve_check(self, request, checks, *, allow_next):
        success = checks[-1].success
        return CheckResolution(
            status="resolved",
            outcome="success" if success else "failure",
            response=TurnResponse(
                narrative="Дверь открывается." if success else "Вы ушибаете плечо.",
                state_updates=[] if success else [
                    StateDelta(character_name="Hero", hp_change=-1),
                ],
                new_location="hall" if success else None,
                dm_thoughts="",
            ),
        )


class ChainDungeonMaster(CheckDungeonMaster):
    def resolve_check(self, request, checks, *, allow_next):
        if checks[-1].success and allow_next:
            return CheckResolution(
                status="check",
                outcome="success",
                check=SkillCheckProposal(
                    ability="dexterity", difficulty=10, reason="Удержите равновесие.",
                ),
            )
        return super().resolve_check(request, checks, allow_next=allow_next)


class SkillCheckAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteSessionStore(Path(self.tmp.name) / "game.db")
        self.store_patch = patch.object(backend, "get_session_store", return_value=self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.roll_value = 12
        self.random_patch = patch.object(
            backend, "SystemRandomSource", side_effect=lambda: SequenceSource([self.roll_value]),
        )
        self.random_patch.start()
        self.addCleanup(self.random_patch.stop)

        backend.sessions.clear()
        backend.game_states.clear()
        backend.game_histories.clear()
        backend.turn_executor = backend.IdempotentExecutor()
        orchestrator = build_orchestrator(
            dm=CheckDungeonMaster(), rules=backend.FakeRulesProvider(),
        )
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: orchestrator
        self.addCleanup(backend.app.dependency_overrides.clear)
        self.client = TestClient(backend.app)
        self.addCleanup(self.client.close)
        created = self.client.post("/sessions", json={"name": "Checks"})
        self.session_id = created.json()["id"]
        self.payload = {
            "action_id": "check-1",
            "expected_revision": 0,
            "character_id": "hero",
            "text": "Открываю тяжёлую дверь.",
        }

    def test_pending_roll_is_persisted_and_applied_once(self):
        first = self.client.post(f"/sessions/{self.session_id}/turns", json=self.payload)
        repeated = self.client.post(f"/sessions/{self.session_id}/turns", json=self.payload)

        self.assertEqual((first.status_code, repeated.status_code), (202, 202))
        self.assertEqual(first.json()["pending_turn"], repeated.json()["pending_turn"])
        self.assertEqual(first.json()["revision"], 0)
        self.assertEqual(self.client.get(f"/sessions/{self.session_id}/state").json()["pending_turn"]["action_id"], "check-1")

        blocked = self.client.post(
            f"/sessions/{self.session_id}/turns",
            json={**self.payload, "action_id": "other", "text": "Жду."},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["code"], "PENDING_CHECK")

        resolved = self.client.post(f"/sessions/{self.session_id}/turns/check-1/roll")
        replayed = self.client.post(f"/sessions/{self.session_id}/turns/check-1/roll")
        self.assertEqual((resolved.status_code, replayed.status_code), (200, 200))
        self.assertEqual(resolved.json(), replayed.json())
        self.assertEqual(resolved.json()["events"][0]["type"], "skill_check_resolved")
        self.assertEqual(resolved.json()["events"][0]["roll"], 12)
        self.assertEqual(resolved.json()["revision"], 1)
        self.assertIsNone(self.client.get(f"/sessions/{self.session_id}/state").json()["pending_turn"])

    def test_unrolled_check_can_be_cancelled(self):
        self.client.post(f"/sessions/{self.session_id}/turns", json=self.payload)
        cancelled = self.client.post(f"/sessions/{self.session_id}/turns/check-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertIsNone(self.store.load_pending_turn(self.session_id))

    def test_success_chain_stops_after_three_checks_and_commits_once(self):
        orchestrator = build_orchestrator(
            dm=ChainDungeonMaster(), rules=backend.FakeRulesProvider(),
        )
        backend.app.dependency_overrides[backend.get_orchestrator] = lambda: orchestrator
        first = self.client.post(f"/sessions/{self.session_id}/turns", json=self.payload)
        self.assertEqual(first.status_code, 202)
        for expected_sequence in (2, 3):
            continued = self.client.post(f"/sessions/{self.session_id}/turns/check-1/roll")
            self.assertEqual(continued.status_code, 202)
            self.assertEqual(continued.json()["pending_turn"]["sequence"], expected_sequence)
            self.assertEqual(continued.json()["revision"], 0)
        final = self.client.post(f"/sessions/{self.session_id}/turns/check-1/roll")
        self.assertEqual(final.status_code, 200)
        self.assertEqual(len([event for event in final.json()["events"] if event["type"] == "skill_check_resolved"]), 3)
        self.assertEqual(final.json()["revision"], 1)

    def test_first_failure_finishes_without_requesting_another_check(self):
        self.roll_value = 1
        self.client.post(f"/sessions/{self.session_id}/turns", json=self.payload)
        result = self.client.post(f"/sessions/{self.session_id}/turns/check-1/roll")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["check_outcome"], "failure")
        self.assertEqual(len([event for event in result.json()["events"] if event["type"] == "skill_check_resolved"]), 1)
        self.assertEqual(result.json()["state"]["characters"][0]["hp"], 9)


if __name__ == "__main__":
    unittest.main()
