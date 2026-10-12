import unittest

from rules_service import RulesService
from rules_service.hybrid import HybridRulesService
from rules_service.rag import RagRulesService
from rules_service.tests.test_rag import BOOK, ConceptEmbedder


class HybridServiceTests(unittest.TestCase):
    def setUp(self):
        keyword = RulesService.from_text(BOOK)
        rag = RagRulesService.from_text(BOOK, ConceptEmbedder())
        self.service = HybridRulesService(keyword, rag)

    def test_russian_paraphrase_uses_rag_when_keyword_empty(self):
        query = "Я хочу перепрыгнуть через пропасть"
        self.assertEqual([], RulesService.from_text(BOOK).search(query))
        top = self.service.search(query, top_k=1)[0]
        self.assertEqual(top.title, "Jumping")

    def test_exact_keyword_still_found(self):
        query = "1d6 bludgeoning damage"
        top = self.service.search(query, top_k=1)[0]
        self.assertEqual(top.title, "Falling")

    def test_chunk_found_by_both_ranks_higher(self):
        query = "jump long jump strength"
        results = self.service.search(query, top_k=2)
        self.assertEqual(results[0].title, "Jumping")
        self.assertLess(len(results), 3)

    def test_get_rules_uses_top_result(self):
        text = self.service.get_rules("fall", top_k=1)
        self.assertTrue(text.startswith("## Falling"))
        self.assertIn("1d6", text)

    def test_empty_book_returns_empty(self):
        keyword = RulesService.from_text("")
        rag = RagRulesService.from_text("", ConceptEmbedder())
        service = HybridRulesService(keyword, rag)
        self.assertEqual(service.search("anything"), [])
        self.assertEqual(service.get_rules("anything"), "")


if __name__ == "__main__":
    unittest.main()
