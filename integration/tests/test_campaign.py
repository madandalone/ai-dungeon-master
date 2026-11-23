import unittest

from ai_dm.errors import ConfigurationError, ProviderError
from ai_dm.models import CampaignStartResponse
from engine import to_public_dict
from engine.dice import Dice, SequenceSource
from engine.models import Character, GameState, InventoryItem
from integration import CampaignRejected, DungeonMasterError, UnmappableResponse, begin_campaign

LOGGER = "integration.campaign"
PLOT = "the abbot feeds the village to the crypt"


def party(*names):
    return tuple(
        Character(f"id-{i}", name, "Rogue", 12, 12, {}, (InventoryItem("dagger", "Кинжал", 2),))
        for i, name in enumerate(names)
    )


def state(*names, revision=0):
    return GameState("s1", revision, revision, "start", party(*(names or ("Элиас",))), {})


def campaign_response(location="Затопленный склеп"):
    return CampaignStartResponse(
        campaign_title="Склеп некроманта", hidden_plot=PLOT, starting_location=location,
        room_context="Вода по колено, в центре зала саркофаг.", intro_narrative="Вы входите в склеп.",
    )


class FakeCampaignDM:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response or campaign_response(), error, []

    def generate_campaign_start(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def begin(dm, game_state=None, theme="Склеп в проклятом лесу", monster="skeleton"):
    return begin_campaign(dm, game_state or state(), theme, Dice(SequenceSource([10])), monster)


class CampaignRequestTests(unittest.TestCase):
    def test_dm_receives_the_party_theme_and_monster(self):
        dm = FakeCampaignDM()
        begin(dm, state("Элиас", "Мира"), theme="  Склеп  ", monster="skeleton")
        request = dm.requests[0]
        self.assertEqual((request.setting_theme, request.monster_encounter), ("Склеп", "skeleton"))
        self.assertEqual([c.name for c in request.characters], ["Элиас", "Мира"])
        self.assertEqual(request.characters[0].inventory, ["Кинжал", "Кинжал"])
        self.assertEqual((request.characters[0].hp, request.characters[0].max_hp), (12, 12))

    def test_a_party_without_characters_is_a_caller_bug(self):
        with self.assertRaises(ValueError):
            begin(FakeCampaignDM(), GameState("s1", 0, 0, "start", (), {}))

    def test_a_blank_theme_is_a_caller_bug(self):
        dm = FakeCampaignDM()
        with self.assertRaises(ValueError):
            begin(dm, theme="   ")
        self.assertEqual(dm.requests, [])

    def test_duplicate_character_names_cannot_start_a_campaign(self):
        dm = FakeCampaignDM()
        with self.assertRaises(UnmappableResponse):
            begin(dm, state("Элиас", "Элиас"))
        self.assertEqual(dm.requests, [])


class CampaignResultTests(unittest.TestCase):
    def test_returns_the_campaign_and_moves_the_party_through_the_engine(self):
        initial = state(revision=0)
        result = begin(FakeCampaignDM(), initial)
        self.assertEqual(result.campaign.title, "Склеп некроманта")
        self.assertEqual(result.campaign.hidden_plot, PLOT)
        self.assertEqual(result.campaign.room_context, "Вода по колено, в центре зала саркофаг.")
        self.assertEqual(result.state.location_id, "Затопленный склеп")
        self.assertEqual((result.state.revision, result.state.turn_number), (1, 1))
        self.assertEqual(initial.location_id, "start", "the input state is not modified")
        self.assertEqual(len(result.events), 1)

    def test_continues_from_the_current_revision(self):
        self.assertEqual(begin(FakeCampaignDM(), state(revision=5)).state.revision, 6)

    def test_the_hidden_plot_is_not_part_of_the_game_state_or_its_public_projection(self):
        result = begin(FakeCampaignDM())
        self.assertNotIn(PLOT, repr(result.state))
        self.assertNotIn(PLOT, repr(to_public_dict(result.state, "Quest")))


class CampaignFailureTests(unittest.TestCase):
    def test_a_dm_failure_keeps_its_category_and_changes_nothing(self):
        dm = FakeCampaignDM(error=ProviderError(category="quota", status_code=402))
        with self.assertLogs(LOGGER, "ERROR") as logs, self.assertRaises(DungeonMasterError) as caught:
            begin(dm)
        self.assertEqual((caught.exception.category, caught.exception.retryable), ("quota", False))
        self.assertIsInstance(caught.exception.__cause__, ProviderError)
        self.assertIn("category=quota", logs.output[0])

    def test_other_dm_errors_get_the_shared_categories(self):
        with self.assertLogs(LOGGER, "ERROR"), self.assertRaises(DungeonMasterError) as caught:
            begin(FakeCampaignDM(error=ConfigurationError("no key")))
        self.assertEqual(caught.exception.category, "configuration")

    def test_a_bug_in_the_dm_is_not_masked(self):
        with self.assertRaises(KeyError):
            begin(FakeCampaignDM(error=KeyError("bug")))

    def test_an_empty_opening_location_is_refused_by_the_engine(self):
        with self.assertLogs(LOGGER, "ERROR"), self.assertRaises(CampaignRejected) as caught:
            begin(FakeCampaignDM(campaign_response(location="")))
        self.assertEqual([e.code for e in caught.exception.errors], ["INVALID_LOCATION"])


class CampaignLogTests(unittest.TestCase):
    def test_logs_carry_ids_and_timings_but_never_the_plot_or_the_theme(self):
        with self.assertLogs(LOGGER, "INFO") as logs:
            begin(FakeCampaignDM(), theme="тайная тема кампании")
        line = logs.output[0]
        for part in ("session=s1", "characters=1", "dm_ms="):
            self.assertIn(part, line)
        joined = "\n".join(logs.output)
        for secret in (PLOT, "тайная тема кампании", "Склеп некроманта"):
            self.assertNotIn(secret, joined)


if __name__ == "__main__":
    unittest.main()


class OrchestratorCampaignTests(unittest.TestCase):
    def orchestrator(self, dm):
        from integration import TurnOrchestrator

        class Rules:
            def get_rules(self, query):
                return ""

        return TurnOrchestrator(dm, Rules())

    def test_a_campaign_capable_dm_is_detected_and_used(self):
        orchestrator = self.orchestrator(FakeCampaignDM())
        self.assertTrue(orchestrator.can_start_campaign)
        result = orchestrator.start_campaign(state(), "Склеп", Dice(SequenceSource([10])))
        self.assertEqual(result.campaign.hidden_plot, PLOT)
        self.assertEqual(result.state.location_id, "Затопленный склеп")

    def test_a_dm_without_the_ability_is_reported_and_refused(self):
        class TurnsOnly:
            def process_turn(self, request):
                raise AssertionError("not used")

        orchestrator = self.orchestrator(TurnsOnly())
        self.assertFalse(orchestrator.can_start_campaign)
        with self.assertRaises(ValueError):
            orchestrator.start_campaign(state(), "Склеп", Dice(SequenceSource([10])))


class AuthoredCampaignTests(unittest.TestCase):
    def authored(self, **overrides):
        from integration import Campaign

        fields = dict(title="Сундук в сторожке", hidden_plot="В сундуке лежит медный ключ.",
                      starting_location="Сторожка", room_context="Одна комната, стол и сундук.",
                      intro_narrative="Вы в пустой сторожке.")
        return Campaign(**{**fields, **overrides})

    def test_moves_the_party_through_the_engine_without_asking_any_dm(self):
        from integration import author_campaign

        initial = state(revision=2)
        result = author_campaign(initial, self.authored(), Dice(SequenceSource([10])))
        self.assertEqual((result.state.location_id, result.state.revision), ("Сторожка", 3))
        self.assertEqual(result.campaign.hidden_plot, "В сундуке лежит медный ключ.")
        self.assertEqual(initial.location_id, "start")

    def test_blank_fields_are_a_caller_bug(self):
        from integration import author_campaign

        for field in ("title", "hidden_plot", "starting_location", "room_context", "intro_narrative"):
            with self.subTest(field=field), self.assertRaises(ValueError) as caught:
                author_campaign(state(), self.authored(**{field: "  "}), Dice(SequenceSource([10])))
            self.assertIn(field, str(caught.exception))

    def test_a_party_is_required(self):
        from integration import author_campaign

        with self.assertRaises(ValueError):
            author_campaign(GameState("s1", 0, 0, "start", (), {}), self.authored(), Dice(SequenceSource([10])))

    def test_the_orchestrator_offers_it_even_to_a_dm_that_cannot_write_campaigns(self):
        from integration import TurnOrchestrator

        class TurnsOnly:
            def process_turn(self, request):
                raise AssertionError("not used")

        class NoRules:
            def get_rules(self, query):
                return ""

        orchestrator = TurnOrchestrator(TurnsOnly(), NoRules())
        self.assertFalse(orchestrator.can_start_campaign)
        result = orchestrator.use_authored_campaign(state(), self.authored(), Dice(SequenceSource([10])))
        self.assertEqual(result.state.location_id, "Сторожка")
