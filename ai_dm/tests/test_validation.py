import unittest

from ai_dm.models import CampaignStartRequest, CampaignStartResponse, TurnResponse
from ai_dm.tests.test_dm_engine import CAMPAIGN, TURN, turn_request
from ai_dm.validation import validate_campaign, validate_turn


class ValidationTests(unittest.TestCase):
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

    def test_game_rule_checks_remain_with_engine(self):
        # Removing a nonexistent item must still be decided by Engine after mapping.
        request = turn_request()
        response = TurnResponse(**{
            **TURN, "state_updates": [{"character_name": "Элиас", "items_removed": ["Меч"]}],
        })
        validate_turn(response, request=request)


if __name__ == "__main__":
    unittest.main()
