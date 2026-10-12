import copy
import json
import unittest
from unittest.mock import patch

from ai_dm.dm_engine import AIDungeonMaster
from ai_dm.errors import InvalidResponseError
from ai_dm.models import CampaignStartRequest
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, completion, fake_client, turn_request
from ai_dm.tests.test_history import action, session
from engine.dice import Dice, SequenceSource
from integration import DungeonMasterError
from rules_service import RulesService
from ai_dm.main import DEMO_RULES


class RussianOutputTests(unittest.TestCase):
    def test_campaign_repairs_all_english_fields_together(self):
        english = {field: "The Drowned Tomb" for field in CAMPAIGN}
        client = fake_client(completion(json.dumps(english)), completion(json.dumps(CAMPAIGN)))
        with patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value={"name": "Skeleton"}):
            result = AIDungeonMaster(client).generate_campaign_start(
                CampaignStartRequest(setting_theme="Sunken crypt", characters=[])
            )
        self.assertEqual(result.model_dump(), CAMPAIGN)
        repair = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
        for field in CAMPAIGN:
            self.assertIn(field, repair)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_each_campaign_field_is_checked_even_with_russian_text(self):
        for field in CAMPAIGN:
            with self.subTest(field=field):
                data = {**CAMPAIGN, field: "Тайны Sunken Crypt"}
                client = fake_client(completion(json.dumps(data)), completion(json.dumps(data)))
                with patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value=None):
                    with self.assertRaises(InvalidResponseError):
                        AIDungeonMaster(client).generate_campaign_start(
                            CampaignStartRequest(setting_theme="Крипта", characters=[])
                        )
                self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_turn_repairs_mixed_prose_new_location_and_item(self):
        mixed = {
            **TURN, "narrative": "Вы enter the crypt.", "dm_thoughts": "Secret",
            "new_location": "Sunken Crypt",
            "state_updates": [{"character_name": "Элиас", "items_added": ["Golden key"]}],
        }
        client = fake_client(completion(json.dumps(mixed)), completion(json.dumps(TURN)))
        result = AIDungeonMaster(client).process_turn(turn_request())
        self.assertEqual(result.model_dump(), TURN)
        repair = client.chat.completions.create.call_args.kwargs["messages"][-1]["content"]
        for field in ("narrative", "dm_thoughts", "new_location", "items_added"):
            self.assertIn(field, repair)

    def test_failed_repair_does_not_change_engine_state_or_history(self):
        game = session()
        before = copy.deepcopy(game)
        mixed = {**TURN, "narrative": "You enter the crypt.",
                 "state_updates": [{"character_name": "Элиас", "hp_change": -3}]}
        client = fake_client(completion(json.dumps(mixed)), completion(json.dumps(mixed)))
        with self.assertRaises(DungeonMasterError) as caught:
            game.play_turn(action(game, 1), AIDungeonMaster(client),
                           RulesService.from_text(DEMO_RULES), Dice(SequenceSource([])))
        self.assertIsInstance(caught.exception.__cause__, InvalidResponseError)
        self.assertEqual(game, before)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_existing_english_references_are_preserved_exactly(self):
        request = turn_request()
        hero = request.game_state.active_characters[0]
        hero.name = "Elias (Rogue)"
        hero.inventory = ["Silver key"]
        data = {
            **TURN, "narrative": "Вы, Elias (Rogue), поднимаете Silver key.",
            "new_location": request.game_state.location,
            "state_updates": [{"character_name": hero.name, "items_added": ["Silver key"],
                               "items_removed": ["Silver key"]}],
        }
        client = fake_client(completion(json.dumps(data)))
        result = AIDungeonMaster(client).process_turn(request)
        self.assertEqual(result.narrative, data["narrative"])
        self.assertEqual(result.state_updates[0].character_name, hero.name)
        self.assertEqual(result.new_location, "crypt")
        self.assertEqual(result.state_updates[0].items_added, ["Silver key"])
        self.assertEqual(result.state_updates[0].items_removed, ["Silver key"])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_campaign_preserves_existing_english_character_in_russian_prose(self):
        hero = turn_request().game_state.active_characters[0]
        hero.name = "Elias"
        data = {**CAMPAIGN, "intro_narrative": "Вы, Elias, входите в склеп."}
        client = fake_client(completion(json.dumps(data)))
        with patch("ai_dm.dm_engine.DnD5eClient.get_monster", return_value=None):
            result = AIDungeonMaster(client).generate_campaign_start(
                CampaignStartRequest(setting_theme="Крипта", characters=[hero])
            )
        self.assertEqual(result.intro_narrative, data["intro_narrative"])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_reference_substrings_do_not_hide_english_words(self):
        request = turn_request()
        request.game_state.active_characters[0].name = "Key"
        mixed = {**TURN, "narrative": "Вы видите Keys."}
        client = fake_client(completion(json.dumps(mixed)), completion(json.dumps(TURN)))
        AIDungeonMaster(client).process_turn(request)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_russian_with_punctuation_numbers_and_empty_notes_needs_no_repair(self):
        data = {**TURN, "narrative": "Вы находите «ключ» — ещё 2 шага!",
                "dm_thoughts": "", "new_location": "Затопленный зал",
                "state_updates": [{"character_name": "Элиас", "items_added": ["Золотой ключ"]}]}
        client = fake_client(completion(json.dumps(data)))
        self.assertEqual(AIDungeonMaster(client).process_turn(turn_request()).model_dump(),
                         {**data, "state_updates": [{"character_name": "Элиас", "hp_change": 0,
                                                   "items_added": ["Золотой ключ"], "items_removed": []}]})
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_language_and_json_errors_share_one_repair(self):
        mixed = completion(json.dumps({**TURN, "narrative": "You enter."}))
        for responses in ((completion("{}"), mixed), (mixed, completion("{}"))):
            with self.subTest(responses=responses):
                client = fake_client(*responses)
                with self.assertRaises(InvalidResponseError):
                    AIDungeonMaster(client).process_turn(turn_request())
                self.assertEqual(client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()

