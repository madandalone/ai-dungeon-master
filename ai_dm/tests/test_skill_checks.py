import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.models import (
    CharacterState,
    CheckResult,
    GameState,
    TurnRequest,
)


def completion(payload):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
    )])


def request():
    return TurnRequest(
        rules_summary="Сложное действие требует проверки.",
        hidden_plot="За дверью находится страж.",
        game_state=GameState(
            location="Зал",
            room_context="Тяжёлая дверь.",
            active_characters=[CharacterState(
                name="Элиас", hp=10, max_hp=10, inventory=[],
                abilities={
                    "strength": 14, "dexterity": 12, "constitution": 10,
                    "intelligence": 10, "wisdom": 10, "charisma": 10,
                },
            )],
        ),
        player_action="Выламываю дверь.",
        acting_character="Элиас",
    )


class SkillCheckGenerationTests(unittest.TestCase):
    def test_plan_can_request_a_valid_engine_check(self):
        client = Mock()
        client.chat.completions.create.return_value = completion({
            "status": "check",
            "response": None,
            "check": {"ability": "strength", "difficulty": 15, "reason": "Дверь тяжёлая."},
        })
        result = AIDungeonMaster(client).plan_turn(request())
        self.assertEqual((result.status, result.check.ability, result.check.difficulty), ("check", "strength", 15))
        call = client.chat.completions.create.call_args.kwargs
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertEqual(call["response_format"]["json_schema"]["name"], "TurnPlan")

    def test_speculative_response_is_discarded_when_check_is_requested(self):
        client = Mock()
        client.chat.completions.create.return_value = completion({
            "status": "check",
            "response": {
                "narrative": "Дверь уже открылась.",
                "state_updates": [{
                    "character_name": "Элиас", "hp_change": 5,
                    "items_added": ["Награда"], "items_removed": [],
                }],
                "new_location": "Сокровищница",
                "dm_thoughts": "",
            },
            "check": {"ability": "strength", "difficulty": 15, "reason": "Дверь тяжёлая."},
        })

        result = AIDungeonMaster(client).plan_turn(request())

        self.assertEqual(result.status, "check")
        self.assertIsNone(result.response)

    def test_failed_check_must_finish_with_failure_outcome(self):
        client = Mock()
        client.chat.completions.create.return_value = completion({
            "status": "resolved",
            "outcome": "failure",
            "check": None,
            "response": {
                "narrative": "Дверь не поддаётся.",
                "state_updates": [],
                "new_location": None,
                "dm_thoughts": "Страж услышал шум.",
            },
        })
        result = AIDungeonMaster(client).resolve_check(
            request(),
            [CheckResult(
                ability="strength", roll=4, modifier=2, total=6,
                difficulty=15, success=False,
            )],
            allow_next=False,
        )
        self.assertEqual((result.status, result.outcome), ("resolved", "failure"))

    def test_explicit_risk_repairs_a_resolved_answer_into_a_check(self):
        resolved = {
            "status": "resolved",
            "check": None,
            "response": {
                "narrative": "Вы ударяетесь о дверь.",
                "state_updates": [{
                    "character_name": "Элиас", "hp_change": -1,
                    "items_added": [], "items_removed": [],
                }],
                "new_location": None,
                "dm_thoughts": "",
            },
        }
        corrected = {
            "status": "check",
            "response": None,
            "check": {"ability": "strength", "difficulty": 15, "reason": "Дверь тяжёлая."},
        }
        client = Mock()
        client.chat.completions.create.side_effect = [completion(resolved), completion(corrected)]
        risky = request().model_copy(update={"player_action": "Пытаюсь выбить дверь, рискуя ушибиться."})
        result = AIDungeonMaster(client).plan_turn(risky)
        self.assertEqual(result.status, "check")
        self.assertEqual(client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
