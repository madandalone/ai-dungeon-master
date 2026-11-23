import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.models import (
    CharacterState,
    CheckResolution,
    CheckResult,
    GameState,
    TurnRequest,
    TurnPlan,
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
    def test_structured_schemas_require_the_payload_for_each_status(self):
        for model in (TurnPlan, CheckResolution):
            with self.subTest(model=model.__name__):
                schema = model.model_json_schema()
                branches = [
                    schema["$defs"][entry["$ref"].rsplit("/", 1)[-1]]
                    for entry in schema["oneOf"]
                ]
                check_branch = next(
                    branch for branch in branches
                    if branch["properties"]["status"].get("const") == "check"
                )
                self.assertIn("check", check_branch["required"])

        with self.assertRaisesRegex(ValueError, "check"):
            TurnPlan(status="check")
        with self.assertRaisesRegex(ValueError, "check"):
            CheckResolution(status="check", outcome="success")

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
        prompt = call["messages"][1]["content"]
        self.assertIn('status="check"', prompt)
        self.assertIn("check с обязательными полями", prompt)
        self.assertNotIn("Тогда верни requires_check: true", prompt)
        self.assertNotIn(json.dumps(TurnPlan.model_json_schema(), ensure_ascii=False, indent=2), prompt)

    def test_missing_check_is_repaired_with_the_strict_contract(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"status": "check"}),
            completion({
                "status": "check",
                "response": None,
                "check": {
                    "ability": "strength",
                    "difficulty": 15,
                    "reason": "Дверь тяжёлая.",
                },
            }),
        ]

        result = AIDungeonMaster(client).plan_turn(request())

        self.assertEqual(result.check.ability, "strength")
        self.assertEqual(client.chat.completions.create.call_count, 2)
        repair = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn("check", repair)
        self.assertIn("Field required", repair)

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
