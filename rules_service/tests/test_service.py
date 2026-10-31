import unittest

from rules_service import RulesService
from rules_service.service import MAX_CHUNK_CHARS, RuleChunk, split_into_chunks

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


NOISE = [RuleChunk(f"Noise {i}", " ".join(f"filler{i}x{j}" for j in range(4))) for i in range(4)]


class Bm25RankingTests(unittest.TestCase):
    def test_matching_more_query_words_beats_repeating_one_word(self):
        echo = RuleChunk("Echo", "jump " * 10)
        both = RuleChunk("Both", "jump and strength")
        self.assertEqual(RulesService([echo, both, *NOISE]).search("jump strength", 1), [both])

    def test_a_long_chunk_with_one_mention_loses_to_a_focused_short_one(self):
        long_chunk = RuleChunk("Long", "swim " + " ".join(f"word{i}" for i in range(200)))
        short_chunk = RuleChunk("Short", "swim in water")
        self.assertEqual(RulesService([long_chunk, short_chunk, *NOISE]).search("swim", 1), [short_chunk])

    def test_a_word_present_in_every_chunk_never_breaks_scoring(self):
        chunks = [RuleChunk(f"T{i}", f"common unique{i}") for i in range(5)]
        service = RulesService(chunks)
        self.assertEqual(len(service.search("common", 5)), 5)
        self.assertEqual(service.search("common unique3", 1), [chunks[3]])

    def test_empty_book_is_searchable(self):
        self.assertEqual(RulesService([]).search("anything"), [])

    def test_ranking_parameters_can_be_changed(self):
        chunks = [RuleChunk("Echo", "jump " * 10), RuleChunk("Both", "jump and strength"), *NOISE]
        nearly_linear = RulesService(chunks, k1=50.0)  # almost no saturation: repetition wins again
        self.assertEqual(nearly_linear.search("jump strength", 1)[0].title, "Echo")


if __name__ == "__main__":
    unittest.main()
