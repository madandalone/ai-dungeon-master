import copy
import unittest

from engine.commands import (
    AddItem,
    AdjustHealth,
    AdjustStat,
    RemoveItem,
    SetLocation,
    SetWorldFlag,
    SkillCheck,
)
from engine.core import apply_action
from engine.dice import Dice, SequenceSource
from engine.events import ActionRejected, HealthChanged, SkillCheckResolved
from engine.models import Character, GameState, InventoryItem, PlayerAction


def character(**overrides):
    data = {
        "id": "ira",
        "name": "Ira",
        "concept": "Harbor scout",
        "hp": 10,
        "max_hp": 10,
        "stats": {"might": 2, "wits": 1},
        "inventory": (InventoryItem("lantern", "lantern", 1),),
    }
    data.update(overrides)
    return Character(**data)


def state(**overrides):
    data = {
        "session_id": "session-1",
        "revision": 3,
        "turn_number": 4,
        "location_id": "quay",
        "characters": (character(),),
        "world_flags": {"tide": "low"},
    }
    data.update(overrides)
    return GameState(**data)


def action(**overrides):
    data = {
        "action_id": "action-1",
        "session_id": "session-1",
        "character_id": "ira",
        "text": "I look along the quay.",
        "expected_revision": 3,
    }
    data.update(overrides)
    return PlayerAction(**data)


def quiet_dice():
    return Dice(SequenceSource(()))


class ApplyActionTests(unittest.TestCase):
    def test_success_copies_state_and_advances_turn_once(self):
        original = state()
        before = copy.deepcopy(original)
        stats = original.characters[0].stats
        result = apply_action(
            original,
            action(),
            [AdjustHealth("ira", -4), AdjustStat("ira", "might", 1)],
            quiet_dice(),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.errors, ())
        self.assertIsNot(result.state, original)
        self.assertEqual(original, before)
        self.assertEqual(stats["might"], 2)
        self.assertEqual(result.state.revision, 4)
        self.assertEqual(result.state.turn_number, 5)
        self.assertEqual(original.revision, 3)
        self.assertEqual(original.turn_number, 4)
        self.assertEqual([event.sequence for event in result.events], [0, 1])

    def test_empty_command_list_still_consumes_one_turn(self):
        original = state()
        result = apply_action(original, action(), [], quiet_dice())

        self.assertTrue(result.accepted)
        self.assertEqual(result.events, ())
        self.assertEqual(result.state.revision, 4)
        self.assertEqual(result.state.turn_number, 5)
        self.assertEqual(result.state.characters, original.characters)

    def test_rejected_action_returns_the_same_state(self):
        original = state()
        result = apply_action(original, action(text="  "), [], quiet_dice())

        self.assertFalse(result.accepted)
        self.assertIs(result.state, original)
        self.assertEqual(result.errors[0].code, "EMPTY_ACTION")
        self.assertIsInstance(result.events[0], ActionRejected)

    def test_validation_errors_are_structured(self):
        cases = [
            (action(character_id="missing"), [], "INVALID_CHARACTER"),
            (action(session_id="other"), [], "SESSION_MISMATCH"),
            (action(expected_revision=1), [], "STALE_REVISION"),
            (action(text=""), [], "EMPTY_ACTION"),
            (action(), [object()], "UNKNOWN_COMMAND"),
            (action(), [AdjustHealth("missing", -1)], "INVALID_TARGET"),
            (action(), [AdjustStat("ira", "luck", 1)], "INVALID_STAT"),
            (action(), [RemoveItem("ira", "rope", 1)], "INVALID_ITEM"),
            (action(), [SetLocation("   ")], "INVALID_LOCATION"),
        ]
        for player_action, commands, code in cases:
            with self.subTest(code=code):
                original = state()
                result = apply_action(original, player_action, commands, quiet_dice())
                self.assertFalse(result.accepted)
                self.assertIs(result.state, original)
                self.assertEqual(result.errors[0].code, code)
                self.assertEqual(result.state.revision, 3)
                self.assertEqual(result.state.turn_number, 4)

    def test_health_is_clamped(self):
        damaged = apply_action(state(), action(), [AdjustHealth("ira", -40)], quiet_dice())
        healed = apply_action(state(characters=(character(hp=8),)), action(), [AdjustHealth("ira", 10)], quiet_dice())

        self.assertEqual(damaged.state.characters[0].hp, 0)
        self.assertEqual(damaged.events[0], HealthChanged("action-1", 0, "ira", 10, 0, -40))
        self.assertEqual(healed.state.characters[0].hp, 10)
        self.assertEqual(healed.events[0].previous_hp, 8)
        self.assertEqual(healed.events[0].new_hp, 10)

    def test_inventory_add_and_remove(self):
        added = apply_action(state(), action(), [AddItem("ira", "lantern", 2)], quiet_dice())
        self.assertEqual(added.state.characters[0].inventory[0].quantity, 3)
        self.assertEqual(added.events[0].new_quantity, 3)

        fresh = apply_action(state(), action(), [AddItem("ira", "rope", 1)], quiet_dice())
        self.assertEqual(fresh.state.characters[0].inventory[1].item_id, "rope")

        removed = apply_action(state(), action(), [RemoveItem("ira", "lantern", 1)], quiet_dice())
        self.assertEqual(removed.state.characters[0].inventory, ())
        self.assertEqual(removed.events[0].new_quantity, 0)

        partial = apply_action(
            state(characters=(character(inventory=(InventoryItem("rope", "rope", 3),)),)),
            action(),
            [RemoveItem("ira", "rope", 2)],
            quiet_dice(),
        )
        self.assertEqual(partial.state.characters[0].inventory[0].quantity, 1)

        too_many = apply_action(state(), action(), [RemoveItem("ira", "lantern", 2)], quiet_dice())
        self.assertFalse(too_many.accepted)
        self.assertEqual(too_many.errors[0].code, "INVALID_ITEM")
        self.assertEqual(too_many.state.characters[0].inventory[0].quantity, 1)

    def test_location_stat_and_flag(self):
        result = apply_action(
            state(),
            action(),
            [
                SetLocation("warehouse"),
                AdjustStat("ira", "wits", -1),
                SetWorldFlag("gate", True),
            ],
            quiet_dice(),
        )

        self.assertEqual(result.state.location_id, "warehouse")
        self.assertEqual(result.state.characters[0].stats["wits"], 0)
        self.assertEqual(result.state.world_flags["gate"], True)
        self.assertEqual(result.state.world_flags["tide"], "low")
        self.assertEqual([type(event).__name__ for event in result.events], [
            "LocationChanged",
            "StatChanged",
            "WorldFlagChanged",
        ])

    def test_skill_check_uses_only_the_injected_source(self):
        success_source = SequenceSource([10])
        success = apply_action(
            state(),
            action(),
            [SkillCheck("ira", "might", 12)],
            Dice(success_source),
        )
        failure_source = SequenceSource([10])
        failure = apply_action(
            state(),
            action(),
            [SkillCheck("ira", "might", 13)],
            Dice(failure_source),
        )

        self.assertEqual(success.events[0], SkillCheckResolved("action-1", 0, "ira", "might", 10, 2, 12, 12, True))
        self.assertFalse(failure.events[0].success)
        self.assertEqual(failure.events[0].modifier, 2)
        self.assertEqual(failure.events[0].total, 12)
        self.assertEqual(success_source.consumed, 1)
        repeated = apply_action(state(), action(), [SkillCheck("ira", "might", 12)], Dice(SequenceSource([10])))
        self.assertEqual(success, repeated)

    def test_invalid_command_rolls_back_the_whole_turn(self):
        original = state()
        flags = original.world_flags
        source = SequenceSource([18])
        result = apply_action(
            original,
            action(),
            [
                SkillCheck("ira", "might", 10),
                AdjustHealth("ira", -3),
                RemoveItem("ira", "lantern", 5),
            ],
            Dice(source),
        )

        self.assertFalse(result.accepted)
        self.assertIs(result.state, original)
        self.assertEqual(original.characters[0].hp, 10)
        self.assertEqual(original.revision, 3)
        self.assertEqual(original.turn_number, 4)
        self.assertIs(original.world_flags, flags)
        self.assertEqual([type(event) for event in result.events], [ActionRejected])
        self.assertEqual(result.errors[0].code, "INVALID_ITEM")
        self.assertEqual(result.errors[0].command_index, 2)
        self.assertEqual(source.consumed, 0)

    def test_add_then_remove_is_visible_inside_the_same_turn(self):
        result = apply_action(
            state(characters=(character(inventory=()),)),
            action(),
            [AddItem("ira", "rope", 1), RemoveItem("ira", "rope", 1)],
            quiet_dice(),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.state.characters[0].inventory, ())
        self.assertEqual(result.state.revision, 4)


if __name__ == "__main__":
    unittest.main()
