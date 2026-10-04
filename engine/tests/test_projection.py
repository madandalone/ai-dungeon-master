import unittest

from engine.models import Character, GameState, InventoryItem
from engine.projection import to_public_dict


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

        self.assertEqual(set(public), {"session_id", "session_name", "location", "characters", "log"})
        self.assertEqual(public["session_id"], "session-1")
        self.assertEqual(public["session_name"], "Harbor Watch")
        self.assertEqual(public["location"], "quay")
        self.assertEqual(public["characters"][0]["hp"], 8)
        self.assertEqual(public["characters"][0]["inventory"][0]["name"], "lantern")
        self.assertEqual(public["log"], [{"kind": "player", "character_id": "ira", "text": "I wait."}])
        rendered = str(public)
        self.assertNotIn("hidden_plot", rendered)
        self.assertNotIn("the gate is false", rendered)
        self.assertNotIn("revision", rendered)
        self.assertNotIn("turn_number", rendered)
        self.assertNotIn("world_flags", rendered)
        self.assertNotIn("narration", rendered)


if __name__ == "__main__":
    unittest.main()
