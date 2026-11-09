import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from pydantic import ValidationError

from ai_dm import main, prompts
from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import InvalidResponseError
from ai_dm.models import CharacterState, TurnHistoryEntry, TurnRequest
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request
from ai_dm.tests.test_history import action, session
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError
from rules_service import RulesService


def response_for(name, hp_change=-2):
    return {**TURN, "state_updates": [{"character_name": name, "hp_change": hp_change}]}


class CharacterNameTests(unittest.TestCase):
    def test_exact_full_name_is_preserved_without_retry(self):
        request = turn_request()
        request.game_state.active_characters[0].name = "Элиас (Плут)"
        client = fake_client(completion(json.dumps(response_for("Элиас (Плут)"))))
        result = AIDungeonMaster(client).process_turn(request)
        self.assertEqual(result.state_updates[0].character_name, "Элиас (Плут)")
        self.assertEqual(client.chat.completions.create.call_count, 1)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn('Допустимые значения state_updates[].character_name: ["Элиас (Плут)"]', prompt)

    def test_shortened_name_is_repaired_before_engine_applies_the_turn(self):
        game = session()
        hero = replace(game.state.characters[0], name="Элиас (Плут)")
        game.state = replace(game.state, characters=(hero,))
        original_state = game.state
        client = fake_client(
            completion(json.dumps(response_for("Элиас"))),
            completion(json.dumps(response_for("Элиас (Плут)"))),
        )
        outcome = game.play_turn(
            action(game, 1), AIDungeonMaster(client),
            RulesService.from_text(main.DEMO_RULES), Dice(SequenceSource([])),
        )
        self.assertTrue(outcome.accepted)
        self.assertEqual(game.state.characters[0].hp, 10)
        self.assertEqual(original_state.characters[0].hp, 12)
        self.assertEqual((game.state.revision, game.state.turn_number), (1, 1))
        self.assertEqual(len(game.history), 1)
        calls = client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        repair = calls[1].kwargs["messages"][-1]["content"]
        self.assertIn('["Элиас"]', repair)
        self.assertIn('Допустимые имена: ["Элиас (Плут)"]', repair)

    def test_repeated_unknown_name_fails_before_mapping_and_keeps_session_unchanged(self):
        for name in ("Призрак", "элиас", "Элиас ", "Элиас (Плут)"):
            with self.subTest(name=name):
                game = session()
                before = copy.deepcopy(game)
                invalid = completion(json.dumps(response_for(name)))
                client = fake_client(invalid, invalid)
                with self.assertRaises(DungeonMasterError) as caught:
                    game.play_turn(
                        action(game, 1), AIDungeonMaster(client),
                        RulesService.from_text(main.DEMO_RULES), Dice(SequenceSource([])),
                    )
                self.assertIsInstance(caught.exception.__cause__, InvalidResponseError)
                self.assertEqual(client.chat.completions.create.call_count, 2)
                self.assertEqual(game, before)

    def test_validation_does_not_guess_between_similar_names(self):
        request = turn_request()
        request.game_state.active_characters = [
            CharacterState(name="Элиас (Плут)", hp=12, max_hp=12, inventory=[]),
            CharacterState(name="Элиас (Маг)", hp=8, max_hp=8, inventory=[]),
        ]
        client = fake_client(
            completion(json.dumps(response_for("Элиас"))),
            completion(json.dumps(response_for("Элиас (Маг)"))),
        )
        result = AIDungeonMaster(client).process_turn(request)
        self.assertEqual(result.state_updates[0].character_name, "Элиас (Маг)")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_schema_and_name_errors_share_one_repair_budget(self):
        unknown = completion(json.dumps(response_for("Призрак")))
        bad_json = completion("{broken")
        for responses in ((bad_json, unknown), (unknown, bad_json)):
            with self.subTest(first=responses[0]):
                client = fake_client(*responses)
                with self.assertRaises(InvalidResponseError):
                    AIDungeonMaster(client).process_turn(turn_request())
                self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_empty_updates_need_no_character_reference(self):
        request = turn_request()
        request.game_state.active_characters = []
        client = fake_client(completion(json.dumps(TURN)))
        self.assertEqual(AIDungeonMaster(client).process_turn(request).state_updates, [])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def prompt_for(self, request):
        client = fake_client(completion(json.dumps(TURN)))
        AIDungeonMaster(client).process_turn(request)
        return client.chat.completions.create.call_args.kwargs["messages"][1]["content"]

    def test_acting_character_is_named_in_the_prompt(self):
        request = turn_request()
        request.game_state.active_characters.append(
            CharacterState(name="Мира (Жрица)", hp=8, max_hp=8, inventory=[]))
        request = TurnRequest(**{**request.model_dump(), "acting_character": "Мира (Жрица)"})
        prompt = self.prompt_for(request)
        self.assertIn('### ДЕЙСТВУЮЩИЙ ПЕРСОНАЖ:\n"Мира (Жрица)"\n', prompt)
        self.assertNotIn(prompts._ACTOR_UNKNOWN, prompt)

    def test_request_without_acting_character_stays_compatible(self):
        request = turn_request()
        self.assertEqual(request.acting_character, "")
        prompt = self.prompt_for(request)
        self.assertIn(f"### ДЕЙСТВУЮЩИЙ ПЕРСОНАЖ:\n{prompts._ACTOR_UNKNOWN}\n", prompt)

    def test_acting_character_must_be_an_exact_active_name(self):
        data = turn_request().model_dump()
        for name in ("Призрак", "элиас", "Элиас "):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValidationError, "acting_character"):
                    TurnRequest(**{**data, "acting_character": name})
        self.assertEqual(TurnRequest(**{**data, "acting_character": "Элиас"}).acting_character, "Элиас")

    def test_history_prompt_omits_opaque_character_ids(self):
        request = turn_request()
        request.history = [TurnHistoryEntry(
            character_id="c0ffee00-0000-4000-8000-000000000000",
            player_action="Осматриваю дверь.", narrative="Дверь заперта.", dm_notes="Ключ у стража.",
        )]
        prompt = self.prompt_for(request)
        self.assertNotIn("c0ffee00", prompt)
        self.assertNotIn("character_id", prompt)
        self.assertIn("Дверь заперта.", prompt)
        self.assertEqual(request.history[0].character_id, "c0ffee00-0000-4000-8000-000000000000")

    def test_demo_does_not_refer_to_an_unaccepted_previous_turn(self):
        client = fake_client(
            completion(json.dumps(CAMPAIGN)),
            completion(json.dumps(response_for("Призрак"))),
            completion(json.dumps(response_for("Призрак"))),
            completion(json.dumps(TURN)),
        )
        output = io.StringIO()
        with patch.object(main, "AIDungeonMaster", return_value=AIDungeonMaster(client)), \
                patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value=None), \
                redirect_stdout(output):
            main.run()
        self.assertIn("Ход не выполнен:", output.getvalue())
        self.assertIn(
            "Ход 2. Игрок: Осмотр: я изучаю саркофаг в поисках тайников или ловушек.",
            output.getvalue(),
        )
        self.assertNotIn("учитывая то, что узнал на предыдущем ходу", output.getvalue())
        self.assertIn("Элиас: Здоровье 12/12", output.getvalue())
        self.assertEqual(client.chat.completions.create.call_count, 4)


if __name__ == "__main__":
    unittest.main()
