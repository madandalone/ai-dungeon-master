import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import InvalidResponseError
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


NO_CHECK = {"needs_check": False, "ability": None, "difficulty": None, "reason": ""}
INCOMPLETE = {"needs_check": True, "ability": None, "difficulty": None, "reason": ""}


def decision(ability, difficulty, reason="Дверь тяжёлая."):
    return {"needs_check": True, "ability": ability, "difficulty": difficulty, "reason": reason}


def safe_response(narrative):
    return {
        "narrative": narrative, "state_updates": [], "new_location": None, "dm_thoughts": "",
        "requires_check": False, "check": None,
    }


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
        client.chat.completions.create.return_value = completion(decision("strength", 15))
        result = AIDungeonMaster(client).plan_turn(request())
        self.assertEqual((result.status, result.check.ability, result.check.difficulty), ("check", "strength", 15))
        self.assertEqual(client.chat.completions.create.call_count, 1, "a check needs only the small decision")
        call = client.chat.completions.create.call_args.kwargs
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertEqual(call["response_format"]["json_schema"]["name"], "CheckDecision")

    def test_a_speculative_response_next_to_the_decision_never_reaches_the_plan(self):
        client = Mock()
        client.chat.completions.create.return_value = completion({
            **decision("strength", 15),
            "response": {
                "narrative": "Дверь уже открылась.",
                "state_updates": [{"character_name": "Элиас", "hp_change": 5, "items_added": ["Награда"],
                                   "items_removed": []}],
                "new_location": "Сокровищница", "dm_thoughts": "",
            },
        })
        result = AIDungeonMaster(client).plan_turn(request())
        self.assertEqual(result.status, "check")
        self.assertIsNone(result.response)

    def test_the_decision_prompt_is_small_and_leaks_neither_plot_nor_history(self):
        from ai_dm.models import TurnHistoryEntry

        client = Mock()
        client.chat.completions.create.return_value = completion(decision("strength", 15))
        busy = request().model_copy(update={
            "history": [TurnHistoryEntry(player_action="Осматриваю комнату.", narrative="Комната пуста.")],
        })
        AIDungeonMaster(client).plan_turn(busy)
        text = " ".join(m["content"] for m in client.chat.completions.create.call_args.kwargs["messages"])
        self.assertIn("Выламываю дверь.", text)
        self.assertIn("Элиас", text)
        self.assertIn('"strength": 14', text)
        for absent in ("За дверью находится страж.", "Осматриваю комнату.", "Комната пуста.", "ИСТОРИЯ"):
            self.assertNotIn(absent, text)

    def test_a_safe_action_takes_a_decision_then_a_narration_with_the_roll_question_closed(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(NO_CHECK),
            completion(safe_response("Вы осматриваете зал: пыль и тишина.")),
        ]
        safe = request().model_copy(update={"player_action": "Осматриваю зал."})
        result = AIDungeonMaster(client).plan_turn(safe)
        self.assertEqual((result.status, result.response.narrative), ("resolved", "Вы осматриваете зал: пыль и тишина."))
        first, second = (c.kwargs for c in client.chat.completions.create.call_args_list)
        self.assertEqual(first["response_format"]["json_schema"]["name"], "CheckDecision")
        self.assertEqual(second["response_format"]["json_schema"]["name"], "TurnResponse")
        narration_prompt = " ".join(m["content"] for m in second["messages"])
        self.assertIn("Проверка характеристики в этом ходе не требуется", narration_prompt)
        self.assertNotIn("Сначала реши, нужна ли проверка", narration_prompt)

    def test_the_word_try_alone_does_not_force_a_roll_on_a_safe_action(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(NO_CHECK),
            completion(safe_response("Вы спокойно осматриваете зал.")),
        ]
        safe = request().model_copy(update={"player_action": "Пытаюсь осмотреть зал."})
        result = AIDungeonMaster(client).plan_turn(safe)
        self.assertEqual((result.status, result.response.narrative), ("resolved", "Вы спокойно осматриваете зал."))

    def test_a_narration_that_asks_for_a_check_after_the_decision_is_repaired(self):
        client = Mock()
        wants_check = safe_response("Вы осматриваете зал.")
        wants_check.update(requires_check=True, check={"stat": "wisdom", "difficulty": 10})
        client.chat.completions.create.side_effect = [
            completion(NO_CHECK),
            completion(wants_check),
            completion(safe_response("Вы осматриваете зал.")),
        ]
        safe = request().model_copy(update={"player_action": "Осматриваю зал."})
        result = AIDungeonMaster(client).plan_turn(safe)
        self.assertEqual((result.status, result.response.requires_check), ("resolved", False))
        self.assertEqual(client.chat.completions.create.call_count, 3)

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

    def test_an_openly_risky_action_cannot_be_waved_through_as_no_check(self):
        client = Mock()
        client.chat.completions.create.side_effect = [completion(NO_CHECK), completion(decision("strength", 15))]
        risky = request().model_copy(update={"player_action": "Пытаюсь выбить дверь, рискуя ушибиться."})
        result = AIDungeonMaster(client).plan_turn(risky)
        self.assertEqual(result.status, "check")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_an_incomplete_decision_is_repaired_with_a_message_that_names_the_gap(self):
        client = Mock()
        client.chat.completions.create.side_effect = [completion(INCOMPLETE), completion(decision("strength", 15))]
        result = AIDungeonMaster(client).plan_turn(request())
        self.assertEqual(result.check.ability, "strength")
        repair = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn("needs_check true requires ability, difficulty, reason", repair)

    def test_an_ability_missing_from_the_hero_sheet_is_repaired(self):
        sheet = request()
        sheet.game_state.active_characters[0].abilities.pop("charisma")
        client = Mock()
        client.chat.completions.create.side_effect = [completion(decision("charisma", 10)), completion(decision("strength", 15))]
        result = AIDungeonMaster(client).plan_turn(sheet)
        self.assertEqual(result.check.ability, "strength")


class ReportedFailureTests(unittest.TestCase):
    """The failure from the field report: a local 7B model, a risky Russian action, HTTP 502 and no clue why."""

    ACTION = "Пытаюсь перепрыгнуть широкую пропасть по скользкому краю."

    def risky(self):
        return request().model_copy(update={"player_action": self.ACTION})

    def test_two_unusable_decisions_end_in_a_controlled_error_that_logs_both_attempts(self):
        client = Mock()
        client.chat.completions.create.side_effect = [completion(NO_CHECK), completion(INCOMPLETE)]
        with self.assertLogs("ai_dm.dm_engine", "WARNING") as logs:
            with self.assertRaises(InvalidResponseError):
                AIDungeonMaster(client).plan_turn(self.risky())
        self.assertEqual(len(logs.output), 2)
        first, second = logs.output
        self.assertIn("operation=plan.decision", first)
        self.assertIn("attempt=1 repair=next", first)
        self.assertIn("явно описал попытку", first)
        self.assertIn("attempt=2 repair=failed", second)
        self.assertIn("needs_check true requires ability, difficulty, reason", second)

    def test_the_logged_reasons_never_contain_the_raw_answer_or_the_plot(self):
        leaky = {"needs_check": True, "ability": None, "difficulty": None, "reason": "SECRET-RAW-TEXT"}
        client = Mock()
        client.chat.completions.create.side_effect = [completion(leaky), completion(leaky)]
        with self.assertLogs("ai_dm.dm_engine", "WARNING") as logs, self.assertRaises(InvalidResponseError):
            AIDungeonMaster(client).plan_turn(self.risky())
        joined = "\n".join(logs.output)
        for secret in ("SECRET-RAW-TEXT", "За дверью находится страж.", self.ACTION):
            self.assertNotIn(secret, joined)

    def test_raw_logging_is_off_by_default_and_opt_in(self):
        for flag, expected in ((None, False), ("1", True)):
            client = Mock()
            client.chat.completions.create.return_value = completion(decision("strength", 15))
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AI_DM_LOG_RAW", None)
                if flag:
                    os.environ["AI_DM_LOG_RAW"] = flag
                with self.assertLogs("ai_dm.dm_engine", "DEBUG") as logs:
                    logging.getLogger("ai_dm.dm_engine").info("sentinel")
                    AIDungeonMaster(client).plan_turn(request())
            has_raw = any("RAW AI RESPONSE" in line or "AI REQUEST" in line for line in logs.output)
            self.assertEqual(has_raw, expected, flag)

    def test_a_good_second_answer_recovers_the_turn_and_logs_the_first_failure(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(NO_CHECK), completion(decision("dexterity", 15, "Скользкий край.")),
        ]
        with self.assertLogs("ai_dm.dm_engine", "WARNING") as logs:
            plan = AIDungeonMaster(client).plan_turn(self.risky())
        self.assertEqual((plan.status, plan.check.ability, plan.check.reason), ("check", "dexterity", "Скользкий край."))
        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
