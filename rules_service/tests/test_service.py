import unittest

from rules_service import RulesService
from rules_service.service import MAX_CHUNK_CHARS, split_into_chunks

BOOK = """# Jumping
Your Strength score determines how far you can jump. A long jump covers a number of feet equal to your Strength score.

# Fall Damage
A fall from a great height ends in injury: you take 1d6 bludgeoning damage for every 10 feet you fall.

## Stealth
Make a Dexterity (Stealth) check to hide from enemies.
"""


class RulesServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RulesService.from_text(BOOK)

    def test_splits_by_headings(self):
        titles = [c.title for c in split_into_chunks(BOOK)]
        self.assertEqual(titles, ["Jumping", "Fall Damage", "Stealth"])

    def test_long_section_is_split_into_bounded_chunks(self):
        text = "# Big\n" + "\n\n".join("word " * 100 for _ in range(10))
        chunks = split_into_chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= MAX_CHUNK_CHARS + 600 for c in chunks))

    def test_search_finds_relevant_chunk_first(self):
        top = self.service.search("I want to jump over a chasm using my strength")[0]
        self.assertEqual(top.title, "Jumping")

    def test_search_without_matches_returns_empty(self):
        self.assertEqual(self.service.search("zzz qqq"), [])
        self.assertEqual(self.service.get_rules("zzz qqq"), "")

    def test_get_rules_respects_limits(self):
        text = self.service.get_rules("fall damage feet", top_k=1)
        self.assertIn("1d6", text)
        self.assertNotIn("Stealth", text)
        self.assertEqual(self.service.get_rules("jump fall hide", max_chars=1), "")


if __name__ == "__main__":
    unittest.main()
