import unittest

from ai_dm.models import StateDelta, TurnRequest, TurnResponse
from engine.dice import Dice, SequenceSource
from engine.models import Character, GameState, InventoryItem, PlayerAction
from integration import DungeonMasterError, TurnOrchestrator, UnmappableResponse
from rules_service import RulesService

BOOK = "# Jumping\nA long jump covers feet equal to your Strength score.\n\n# Fall Damage\nTake 1d6 damage per 10 feet fallen."


class FakeDM:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def process_turn(self, request: TurnRequest) -> TurnResponse:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


def response(updates=(), location=None):
    return TurnResponse(narrative="Стрела попадает в плечо.", state_updates=list(updates), new_location=location, dm_thoughts="secret note")


def make_state():
    elias = Character("c1", "Элиас", "Плут", 12, 12, {"str": 2}, (InventoryItem("dagger", "Кинжал", 1),))
    return GameState("s1", 3, 1, "tavern", (elias,), {})


def make_action(revision=3, text="I jump over the chasm, long jump"):
    return PlayerAction("a1", "s1", "c1", text, revision)


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.rules = RulesService.from_text(BOOK)
        self.dice = Dice(SequenceSource([10]))

    def play(self, dm, action=None, state=None):
        return TurnOrchestrator(dm, self.rules).play_turn(state or make_state(), action or make_action(), "cult plot", self.dice)

    def test_accepted_turn_applies_dm_changes_through_engine(self):
        dm = FakeDM(response([StateDelta(character_name="Элиас", hp_change=-5, items_added=["Ключ", "Ключ"], items_removed=["Кинжал"])], "cellar"))
        out = self.play(dm)
        self.assertTrue(out.accepted)
        hero = out.state.characters[0]
        self.assertEqual((hero.hp, out.state.location_id, out.state.revision), (7, "cellar", 4))
        self.assertEqual({i.item_id: i.quantity for i in hero.inventory if i.quantity > 0}, {"Ключ": 2})
        self.assertEqual(out.narration, "Стрела попадает в плечо.")
        self.assertEqual(out.dm_notes, "secret note")

    def test_dm_receives_rules_hidden_plot_and_mapped_state(self):
        dm = FakeDM(response())
        self.play(dm)
        req = dm.requests[0]
        self.assertIn("long jump", req.rules_summary)
        self.assertEqual(req.hidden_plot, "cult plot")
        self.assertEqual(req.game_state.location, "tavern")
        self.assertEqual(req.game_state.active_characters[0].inventory, ["Кинжал"])

    def test_no_matching_rules_uses_fallback(self):
        dm = FakeDM(response())
        TurnOrchestrator(dm, self.rules).play_turn(make_state(), PlayerAction("a1", "s1", "c1", "zzz", 3), "p", self.dice)
        self.assertIn("No specific rules", dm.requests[0].rules_summary)

    def test_stale_revision_rejected_without_calling_llm(self):
        dm = FakeDM(response())
        out = self.play(dm, make_action(revision=1))
        self.assertFalse(out.accepted)
        self.assertEqual(out.errors[0].code, "STALE_REVISION")
        self.assertEqual(dm.requests, [])
        self.assertIsNone(out.narration)

    def test_engine_rejection_drops_narration_and_keeps_state(self):
        state = make_state()
        out = self.play(FakeDM(response([StateDelta(character_name="Элиас", items_removed=["Меч"])])), state=state)
        self.assertFalse(out.accepted)
        self.assertIs(out.state, state)
        self.assertIsNone(out.narration)
        self.assertEqual(out.errors[0].code, "INVALID_ITEM")

    def test_unknown_character_raises(self):
        with self.assertRaises(UnmappableResponse):
            self.play(FakeDM(response([StateDelta(character_name="Призрак", hp_change=-1)])))

    def test_dm_failure_is_wrapped(self):
        with self.assertRaises(DungeonMasterError):
            self.play(FakeDM(error=TimeoutError("llm down")))

    def test_empty_response_still_advances_turn(self):
        out = self.play(FakeDM(response()))
        self.assertTrue(out.accepted)
        self.assertEqual((out.state.revision, out.state.turn_number), (4, 2))


if __name__ == "__main__":
    unittest.main()
