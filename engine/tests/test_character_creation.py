import unittest

from engine import add_character
from engine.models import Character, GameState


def state() -> GameState:
    return GameState(
        session_id="session-1",
        revision=2,
        turn_number=4,
        location_id="start",
        characters=(),
        world_flags={},
    )


def character(character_id="hero", name="Элиас") -> Character:
    return Character(
        id=character_id,
        name=name,
        concept="Плут",
        hp=10,
        max_hp=10,
        stats={"ловкость": 3},
        inventory=(),
    )


class CharacterCreationTests(unittest.TestCase):
    def test_character_is_added_without_consuming_a_turn(self):
        original = state()

        result = add_character(original, character(), expected_revision=2)

        self.assertTrue(result.accepted)
        self.assertEqual(original.characters, ())
        self.assertEqual(result.state.revision, 3)
        self.assertEqual(result.state.turn_number, 4)
        self.assertEqual(result.state.characters[0].name, "Элиас")

    def test_stale_revision_is_rejected(self):
        original = state()

        result = add_character(original, character(), expected_revision=1)

        self.assertFalse(result.accepted)
        self.assertIs(result.state, original)
        self.assertEqual(result.errors[0].code, "STALE_REVISION")

    def test_duplicate_id_is_rejected(self):
        original = state()
        first = add_character(original, character(), expected_revision=2)

        result = add_character(first.state, character(), expected_revision=3)

        self.assertFalse(result.accepted)
        self.assertEqual(result.errors[0].code, "DUPLICATE_CHARACTER")

    def test_invalid_character_is_rejected(self):
        result = add_character(state(), character(name="  "), expected_revision=2)

        self.assertFalse(result.accepted)
        self.assertEqual(result.errors[0].code, "INVALID_CHARACTER")


if __name__ == "__main__":
    unittest.main()
