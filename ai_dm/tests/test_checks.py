import json
import unittest

from ai_dm import prompts
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import InvalidResponseError
from ai_dm.models import CharacterState, CheckResult, TurnRequest, TurnResponse
from ai_dm.tests.test_dm_engine import TURN, completion, fake_client, turn_request
from ai_dm.validation import MAX_DIFFICULTY, MIN_DIFFICULTY, validate_turn


STATS = {"Ловкость": 3, "Сила": 1}
CHECK = {**TURN, "requires_check": True, "check": {"stat": "Ловкость", "difficulty": 15},
         "narrative": "", "dm_thoughts": ""}
RESULT = CheckResult(stat="Ловкость", difficulty=15, roll=12, modifier=3, total=15, success=True)


def check_request(**changes):
    """A first-stage request: the acting hero has stats, so a check can be rolled."""
    data = turn_request().model_dump()
    data["game_state"]["active_characters"] = [
        CharacterState(name="Элиас", hp=12, max_hp=12, inventory=[], stats=STATS).model_dump()
    ]
    return TurnRequest(**{**data, "acting_character": "Элиас", "player_action": "Перепрыгиваю провал.",
                          **changes})


def check_response(**changes):
    return TurnResponse(**{**CHECK, **changes})


class CheckValidationTests(unittest.TestCase):
    def test_valid_check_request_passes_with_empty_narrative(self):
        result = validate_turn(check_response(), request=check_request())
        self.assertTrue(result.requires_check)
        self.assertEqual((result.check.stat, result.check.difficulty), ("Ловкость", 15))

    def test_empty_narrative_is_allowed_only_while_a_check_is_pending(self):
        with self.assertRaisesRegex(ValueError, "narrative"):
            validate_turn(TurnResponse(**{**TURN, "narrative": ""}), request=check_request())
        with self.assertRaisesRegex(ValueError, "narrative"):
            validate_turn(check_response(narrative="You jump."), request=check_request())

    def test_stat_must_be_an_exact_stat_of_the_acting_character(self):
        request = check_request()
        request.game_state.active_characters.append(
            CharacterState(name="Мира", hp=8, max_hp=8, inventory=[], stats={"Мудрость": 4}))
        for stat in ("Мудрость", "ловкость", "Ловкость ", "dexterity", ""):
            with self.subTest(stat=stat):
                with self.assertRaises(ValueError) as caught:
                    validate_turn(check_response(check={"stat": stat, "difficulty": 15}), request=request)
                self.assertIn('Допустимые характеристики: ["Ловкость", "Сила"]', str(caught.exception))

    def test_difficulty_must_be_within_the_allowed_range(self):
        for difficulty in (MIN_DIFFICULTY, MAX_DIFFICULTY):
            with self.subTest(difficulty=difficulty):
                validate_turn(check_response(check={"stat": "Сила", "difficulty": difficulty}),
                              request=check_request())
        for difficulty in (-5, 0, MIN_DIFFICULTY - 1, MAX_DIFFICULTY + 1, 1000):
            with self.subTest(difficulty=difficulty):
                with self.assertRaisesRegex(ValueError, "check.difficulty должно быть целым числом от 5 до 30"):
                    validate_turn(check_response(check={"stat": "Сила", "difficulty": difficulty}),
                                  request=check_request())

    def test_check_is_unavailable_without_an_actor_or_stats(self):
        no_actor = check_request(acting_character="")
        no_stats = check_request()
        no_stats.game_state.active_characters[0].stats = {}
        for request in (no_actor, no_stats):
            with self.subTest(actor=request.acting_character):
                with self.assertRaisesRegex(ValueError, "Проверка недоступна"):
                    validate_turn(check_response(), request=request)

    def test_flag_and_parameters_must_agree(self):
        with self.assertRaisesRegex(ValueError, "requires_check: true требует объект check"):
            validate_turn(check_response(check=None), request=check_request())
        with self.assertRaisesRegex(ValueError, "check задан при requires_check: false"):
            validate_turn(check_response(requires_check=False, narrative="Ты прыгаешь."),
                          request=check_request())

    def test_pending_check_must_not_propose_consequences(self):
        for changes in ({"state_updates": [{"character_name": "Элиас", "hp_change": -3}]},
                        {"new_location": "Дальний берег"}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "не предлагай изменения"):
                    validate_turn(check_response(**changes), request=check_request())

    def test_second_stage_must_describe_the_outcome_not_ask_again(self):
        request = check_request(check_result=RESULT)
        outcome = TurnResponse(**{
            **TURN, "narrative": "Ты перелетаешь провал.",
            "state_updates": [{"character_name": "Элиас", "hp_change": -1}],
        })
        self.assertEqual(validate_turn(outcome, request=request).state_updates[0].hp_change, -1)
        for response in (check_response(), check_response(requires_check=False, narrative="Ты прыгаешь.")):
            with self.subTest(requires_check=response.requires_check):
                with self.assertRaisesRegex(ValueError, "уже проведена"):
                    validate_turn(response, request=request)

    def test_stat_names_are_allowed_in_russian_prose(self):
        request = check_request()
        request.game_state.active_characters[0].stats = {"dexterity": 3}
        response = TurnResponse(**{**TURN, "narrative": "Тебе пригодится dexterity."})
        self.assertEqual(validate_turn(response, request=request).narrative, "Тебе пригодится dexterity.")


class CheckPromptTests(unittest.TestCase):
    def prompt_for(self, request):
        client = fake_client(completion(json.dumps(TURN)))
        AIDungeonMaster(client).process_turn(request)
        return client.chat.completions.create.call_args.kwargs["messages"][1]["content"]

    def test_first_stage_offers_the_actors_stats_and_difficulty_range(self):
        prompt = self.prompt_for(check_request())
        self.assertIn('Характеристики действующего персонажа: {"Ловкость": 3, "Сила": 1}.', prompt)
        self.assertIn("целое число от 5 до 30", prompt)
        self.assertNotIn("уже бросил кубик", prompt)
        self.assertNotIn(prompts._CHECK_UNAVAILABLE, prompt)

    def test_check_is_not_offered_without_an_actor_or_stats(self):
        no_stats = check_request()
        no_stats.game_state.active_characters[0].stats = {}
        for request in (turn_request(), no_stats):
            with self.subTest(actor=request.acting_character):
                prompt = self.prompt_for(request)
                self.assertIn(f"### ПРОВЕРКА ХАРАКТЕРИСТИКИ:\n{prompts._CHECK_UNAVAILABLE}\n", prompt)
                self.assertNotIn("Сначала реши, нужна ли проверка", prompt)

    def test_second_stage_reports_the_engine_roll(self):
        prompt = self.prompt_for(check_request(check_result=RESULT))
        self.assertIn(json.dumps(RESULT.model_dump(), ensure_ascii=False), prompt)
        self.assertIn("Опиши исход строго по полю success", prompt)
        self.assertNotIn("Сначала реши, нужна ли проверка", prompt)

    def test_response_schema_lets_the_model_request_a_check(self):
        schema = TurnResponse.model_json_schema()
        self.assertEqual(list(schema["properties"])[:2], ["requires_check", "check"])
        self.assertEqual(set(schema["$defs"]["CheckRequest"]["properties"]), {"stat", "difficulty"})
        self.assertEqual(TurnResponse(**{k: v for k, v in TURN.items() if k not in ("requires_check", "check")}),
                         TurnResponse(**TURN))


class ProcessTurnCheckTests(unittest.TestCase):
    """Both stages through AIDungeonMaster.process_turn; only the LLM is replaced.

    Rolling the die and joining the two stages belongs to the caller (the turn orchestrator).
    """

    def test_first_stage_returns_a_validated_check_request(self):
        client = fake_client(completion(json.dumps(CHECK)))
        result = AIDungeonMaster(client).process_turn(check_request())
        self.assertTrue(result.requires_check)
        self.assertEqual((result.check.stat, result.check.difficulty), ("Ловкость", 15))
        self.assertEqual((result.state_updates, result.new_location), ([], None))
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_second_stage_returns_the_described_outcome(self):
        outcome = {**TURN, "narrative": "Ты срываешься и больно ударяешься о камни.",
                   "state_updates": [{"character_name": "Элиас", "hp_change": -4}]}
        failed = RESULT.model_copy(update={"roll": 3, "total": 6, "success": False})
        client = fake_client(completion(json.dumps(outcome)))
        result = AIDungeonMaster(client).process_turn(check_request(check_result=failed))
        self.assertFalse(result.requires_check)
        self.assertEqual(result.state_updates[0].hp_change, -4)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn('"roll": 3', prompt)
        self.assertIn('"success": false', prompt)

    def test_invalid_check_is_repaired_once(self):
        invalid = {**CHECK, "check": {"stat": "dexterity", "difficulty": 99}}
        client = fake_client(completion(json.dumps(invalid)), completion(json.dumps(CHECK)))
        result = AIDungeonMaster(client).process_turn(check_request())
        self.assertEqual(result.check.stat, "Ловкость")
        repair = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn('check.stat "dexterity" нет у персонажа "Элиас"', repair)
        self.assertIn("получено 99", repair)

    def test_repeated_invalid_check_fails_without_changing_the_request(self):
        invalid = {**CHECK, "check": {"stat": "Харизма", "difficulty": 15}}
        request = check_request()
        before = request.model_copy(deep=True)
        client = fake_client(completion(json.dumps(invalid)), completion(json.dumps(invalid)))
        with self.assertRaises(InvalidResponseError):
            AIDungeonMaster(client).process_turn(request)
        self.assertEqual(request, before)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_second_check_request_after_the_roll_is_repaired_then_refused(self):
        client = fake_client(completion(json.dumps(CHECK)), completion(json.dumps(CHECK)))
        with self.assertRaises(InvalidResponseError):
            AIDungeonMaster(client).process_turn(check_request(check_result=RESULT))
        repair = client.chat.completions.create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertIn("Проверка в этом ходе уже проведена", repair)

if __name__ == "__main__":
    unittest.main()
