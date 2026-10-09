import tempfile
import unittest

import numpy as np

from rules_service import RagRulesService, RulesService
from rules_service.service import split_into_chunks

BOOK = """# Movement
## Jumping
Your Strength score determines how far you can jump. A long jump covers feet equal to your Strength score.

## Falling
A fall from a great height ends in injury: you take 1d6 bludgeoning damage for every 10 feet you fall.

# Combat
## Stealth
Make a Dexterity (Stealth) check to hide from enemies.
"""

CONCEPTS = {
    "jump": 0, "jumping": 0, "leap": 0, "перепрыгнуть": 0, "прыгнуть": 0, "пропасть": 0,
    "fall": 1, "falling": 1, "упасть": 1, "падение": 1,
    "stealth": 2, "hide": 2, "спрятаться": 2, "незаметно": 2,
}


class ConceptEmbedder:
    """Test double: words of the same meaning (any language) share one dimension."""

    name = "concept-fake"

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        out = np.zeros((len(texts), len(set(CONCEPTS.values())) + 1), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.lower().replace("(", " ").replace(")", " ").replace(".", " ").split():
                out[row, CONCEPTS.get(word, 3)] += 1.0 if word in CONCEPTS else 0.01
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class RagServiceTests(unittest.TestCase):
    def setUp(self):
        self.embedder = ConceptEmbedder()
        self.service = RagRulesService.from_text(BOOK, self.embedder)

    def test_finds_chunk_across_languages_where_keyword_search_fails(self):
        query = "Я хочу перепрыгнуть через пропасть"
        self.assertEqual(RulesService.from_text(BOOK).search(query), [])
        self.assertEqual(self.service.search(query)[0].title, "Jumping")

    def test_ranks_by_meaning(self):
        self.assertEqual(self.service.search("упасть с высоты", top_k=1)[0].title, "Falling")
        self.assertEqual(self.service.search("спрятаться незаметно", top_k=1)[0].title, "Stealth")

    def test_min_score_filters_irrelevant_results(self):
        self.assertEqual(self.service.search("абракадабра", min_score=0.9), [])

    def test_get_rules_formats_and_limits(self):
        text = self.service.get_rules("leap", top_k=1)
        self.assertTrue(text.startswith("## Jumping"))
        self.assertEqual(self.service.get_rules("leap", max_chars=1), "")

    def test_empty_query_and_empty_book(self):
        self.assertEqual(self.service.search("   "), [])
        self.assertEqual(RagRulesService.from_text("", self.embedder).search("jump"), [])

    def test_cache_avoids_re_embedding_and_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as cache:
            RagRulesService.from_text(BOOK, self.embedder, cache)
            calls = self.embedder.calls
            cached = RagRulesService.from_text(BOOK, self.embedder, cache)
            self.assertEqual(self.embedder.calls, calls)
            self.assertEqual(cached.search("leap", top_k=1)[0].title, "Jumping")
            RagRulesService.from_text(BOOK + "\n# New\nNew rule text.", self.embedder, cache)
            self.assertGreater(self.embedder.calls, calls)


class ChunkingTests(unittest.TestCase):
    def test_heading_path_is_kept(self):
        chunks = {c.title: c.path for c in split_into_chunks(BOOK)}
        self.assertEqual(chunks["Jumping"], "Movement > Jumping")
        self.assertEqual(chunks["Stealth"], "Combat > Stealth")

    def test_chunks_respect_max_chars_even_for_one_huge_paragraph(self):
        table = "\n".join(f"| row {i} | value |" for i in range(200))
        chunks = split_into_chunks(f"# Table\n{table}", max_chars=300)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c.text) <= 300 for c in chunks))


if __name__ == "__main__":
    unittest.main()
