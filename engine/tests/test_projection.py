import unittest

from engine.events import (
    ActionRejected,
    HealthChanged,
    ItemAdded,
    ItemRemoved,
    LocationChanged,
    SkillCheckResolved,
    StatChanged,
    WorldFlagChanged,
)
from engine.models import Character, GameState, InventoryItem
from engine.projection import to_public_dict, to_public_events


class PublicProjectionTests(unittest.TestCase):
    def test_public_dict_keeps_only_the_frontend_contract(self):
        game = GameState(
            session_id="session-1",
            revision=7,
            turn_number=2,
            location_id="quay",
            characters=(
                Character(
                    id="ira",
                    name="Ira",
                    concept="Harbor scout",
                    hp=8,
                    max_hp=10,
                    stats={"might": 2},
                    inventory=(InventoryItem("lantern", "lantern", 1),),
                ),
            ),
            world_flags={"hidden_plot": "the gate is false", "tide": "low"},
        )
        public = to_public_dict(
            game,
            session_name="Harbor Watch",
            log=({"kind": "player", "character_id": "ira", "text": "I wait.", "hidden_plot": "nope"},),
        )

        self.assertEqual(
            set(public),
            {"session_id", "session_name", "revision", "turn_number", "location", "characters", "log"},
        )
        self.assertEqual(public["session_id"], "session-1")
        self.assertEqual(public["session_name"], "Harbor Watch")
        self.assertEqual(public["revision"], 7)
        self.assertEqual(public["turn_number"], 2)
        self.assertEqual(public["location"], "quay")
        self.assertEqual(public["characters"][0]["hp"], 8)
        self.assertEqual(public["characters"][0]["inventory"][0]["name"], "lantern")
        self.assertEqual(public["log"], [{"kind": "player", "character_id": "ira", "text": "I wait."}])
        rendered = str(public)
        self.assertNotIn("hidden_plot", rendered)
        self.assertNotIn("the gate is false", rendered)
        self.assertNotIn("world_flags", rendered)
        self.assertNotIn("narration", rendered)

    def test_public_events_are_typed_and_world_flags_stay_private(self):
        events = (
            HealthChanged("a1", 0, "hero", 10, 0, -40),
            ItemAdded("a1", 1, "hero", "key", 1, 1),
            ItemRemoved("a1", 2, "hero", "torch", 1, 0),
            LocationChanged("a1", 3, "start", "crypt"),
            StatChanged("a1", 4, "hero", "might", 2, 3, 1),
            SkillCheckResolved("a1", 5, "hero", "might", 12, 3, 15, 14, True),
            WorldFlagChanged("a1", 6, "secret_door", None, True),
            ActionRejected("a2", 0, "STALE_REVISION"),
        )

        public = to_public_events(events)

        self.assertEqual(
            [event["type"] for event in public],
            [
                "health_changed",
                "item_added",
                "item_removed",
                "location_changed",
                "stat_changed",
                "skill_check_resolved",
                "action_rejected",
            ],
        )
        self.assertEqual(public[0]["delta"], -10)
        self.assertEqual(public[3]["location"], "crypt")
        self.assertTrue(public[5]["success"])
        self.assertEqual(public[6]["code"], "STALE_REVISION")
        self.assertNotIn("secret_door", repr(public))


if __name__ == "__main__":
    unittest.main()
