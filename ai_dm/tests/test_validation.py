import unittest

from ai_dm.models import CampaignStartRequest, CampaignStartResponse, CharacterState, TurnResponse
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, turn_request
from ai_dm.validation import validate_campaign, validate_turn


class ValidationTests(unittest.TestCase):
    def test_normalization_returns_copies_and_preserves_inputs_even_on_failure(self):
        request = turn_request()
        campaign_request = CampaignStartRequest(setting_theme="Крипта", characters=request.game_state.active_characters)
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                turn = TurnResponse(**{
                    **TURN, "narrative": "Вы видите cклеп.",
                    "state_updates": [{"character_name": "Элиас", "items_added": ["Cтарый ключ"]}],
                    "dm_thoughts": "English" if invalid else "Cтраж ждёт.",
                })
                campaign = CampaignStartResponse(**{
                    **CAMPAIGN, "intro_narrative": "Вы видите cклеп.",
                    "hidden_plot": "English" if invalid else "Cтраж ждёт.",
                })
                values = (request, campaign_request, turn, campaign)
                before = [value.model_copy(deep=True) for value in values]
                if invalid:
                    with self.assertRaises(ValueError):
                        validate_turn(turn, request=request)
                    with self.assertRaises(ValueError):
                        validate_campaign(campaign, request=campaign_request)
                else:
                    normalized_turn = validate_turn(turn, request=request)
                    normalized_campaign = validate_campaign(campaign, request=campaign_request)
                    self.assertEqual(normalized_turn.narrative, "Вы видите склеп.")
                    self.assertEqual(normalized_campaign.intro_narrative, "Вы видите склеп.")
                    self.assertIsNot(normalized_turn.state_updates[0], turn.state_updates[0])
                self.assertEqual(list(values), before)

    def test_valid_campaign_and_turn_preserve_inputs(self):
        request = turn_request()
        campaign_request = CampaignStartRequest(setting_theme="Крипта", characters=request.game_state.active_characters)
        campaign = CampaignStartResponse(**CAMPAIGN)
        turn = TurnResponse(**TURN)
        before = [value.model_copy(deep=True) for value in (request, campaign_request, campaign, turn)]
        validate_campaign(campaign, request=campaign_request)
        validate_turn(turn, request=request)
        self.assertEqual([request, campaign_request, campaign, turn], before)

    def test_turn_validation_uses_the_supplied_session_not_previous_call(self):
        first = turn_request()
        second = first.model_copy(deep=True)
        second.game_state.active_characters[0].name = "Мира"
        response = TurnResponse(**{**TURN, "state_updates": [{"character_name": "Элиас"}]})
        validate_turn(response, request=first)
        with self.assertRaisesRegex(ValueError, "неизвестные character_name"):
            validate_turn(response, request=second)

    def test_all_turn_validation_errors_are_reported_together(self):
        response = TurnResponse(**{
            **TURN, "narrative": "English", "dm_thoughts": "Secret", "new_location": "Crypt",
            "state_updates": [{"character_name": "Unknown", "items_added": ["Key"]}],
        })
        with self.assertRaises(ValueError) as caught:
            validate_turn(response, request=turn_request())
        for field in ("narrative", "dm_thoughts", "new_location", "items_added", "character_name"):
            self.assertIn(field, str(caught.exception))

    def test_all_campaign_text_fields_are_checked(self):
        request = CampaignStartRequest(setting_theme="Крипта", characters=[])
        response = CampaignStartResponse(**{field: "English" for field in CAMPAIGN})
        with self.assertRaises(ValueError) as caught:
            validate_campaign(response, request=request)
        for field in CAMPAIGN:
            self.assertIn(field, str(caught.exception))

    def test_removing_items_the_character_does_not_hold_is_rejected(self):
        request = turn_request()
        request.game_state.active_characters[0].inventory = ["Кинжал", "Факел", "Факел"]
        for removed, missing in [
            (["Меч"], '["Меч"]'),
            (["Факел", "Факел", "Факел"], '["Факел"]'),
            (["кинжал"], '["кинжал"]'),  # Exact names only, as Engine maps them.
        ]:
            with self.subTest(removed=removed):
                response = TurnResponse(**{
                    **TURN, "state_updates": [{"character_name": "Элиас", "items_removed": removed}],
                })
                with self.assertRaises(ValueError) as caught:
                    validate_turn(response, request=request)
                message = str(caught.exception)
                self.assertIn(f"которых у персонажа нет: {missing}", message)
                self.assertIn('Инвентарь персонажа: ["Кинжал", "Факел", "Факел"]', message)

    def test_removals_across_deltas_follow_engine_order(self):
        request = turn_request()
        request.game_state.active_characters[0].inventory = ["Факел"]
        valid = [
            [{"character_name": "Элиас", "items_removed": ["Факел"]}],
            [{"character_name": "Элиас", "items_added": ["Ключ"], "items_removed": ["Ключ", "Факел"]}],
            [{"character_name": "Элиас", "items_added": ["Ключ"]},
             {"character_name": "Элиас", "items_removed": ["Ключ"]}],
        ]
        for updates in valid:
            with self.subTest(updates=updates):
                validate_turn(TurnResponse(**{**TURN, "state_updates": updates}), request=request)
        invalid = [
            [{"character_name": "Элиас", "items_removed": ["Факел"]},
             {"character_name": "Элиас", "items_removed": ["Факел"]}],
            [{"character_name": "Элиас", "items_removed": ["Ключ"]},
             {"character_name": "Элиас", "items_added": ["Ключ"]}],
        ]
        for updates in invalid:
            with self.subTest(updates=updates):
                with self.assertRaisesRegex(ValueError, "которых у персонажа нет"):
                    validate_turn(TurnResponse(**{**TURN, "state_updates": updates}), request=request)

    def test_inventory_is_checked_per_character(self):
        request = turn_request()
        request.game_state.active_characters = [
            CharacterState(name="Элиас", hp=12, max_hp=12, inventory=["Кинжал"]),
            CharacterState(name="Мира", hp=8, max_hp=8, inventory=[]),
        ]
        response = TurnResponse(**{
            **TURN, "state_updates": [{"character_name": "Мира", "items_removed": ["Кинжал"]}],
        })
        with self.assertRaises(ValueError) as caught:
            validate_turn(response, request=request)
        self.assertIn('state_updates для "Мира"', str(caught.exception))
        self.assertNotIn('"Элиас"', str(caught.exception))

    def test_unknown_character_is_not_also_reported_as_missing_items(self):
        response = TurnResponse(**{
            **TURN, "state_updates": [{"character_name": "Призрак", "items_removed": ["Меч"]}],
        })
        with self.assertRaises(ValueError) as caught:
            validate_turn(response, request=turn_request())
        self.assertIn("неизвестные character_name", str(caught.exception))
        self.assertNotIn("которых у персонажа нет", str(caught.exception))

    def test_blank_location_and_items_are_reported_as_blank_not_as_language(self):
        response = TurnResponse(**{
            **TURN, "new_location": "  ",
            "state_updates": [{"character_name": "Элиас", "items_added": ["", "Ключ"]}],
        })
        with self.assertRaises(ValueError) as caught:
            validate_turn(response, request=turn_request())
        message = str(caught.exception)
        self.assertIn("Пустые значения недопустимы, поля: new_location, state_updates[0].items_added[0]", message)
        self.assertNotIn("по-русски", message)

    def test_health_rules_remain_with_engine(self):
        # Engine clamps HP; the DM does not second-guess the size of a change.
        response = TurnResponse(**{
            **TURN, "state_updates": [{"character_name": "Элиас", "hp_change": -100}],
        })
        validate_turn(response, request=turn_request())


if __name__ == "__main__":
    unittest.main()
