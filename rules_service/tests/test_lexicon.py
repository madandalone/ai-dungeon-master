import re
import unittest
from pathlib import Path

from rules_service import RulesService
from rules_service.build import build_rules_service
from rules_service.evaluation import evaluate, load_cases
from rules_service.hybrid import HybridRulesService
from rules_service.lexicon import ENTRIES, MIN_STEM_LENGTH, LexiconRulesService, expand_query
from rules_service.service import load_text, split_into_chunks, tokenize
from rules_service.tests.test_rag import ConceptEmbedder
from rules_service.tests.test_service import BOOK

PACKAGE = Path(__file__).resolve().parents[1]
BOOK_FILE = PACKAGE.parent / "data" / "srd.md"
HOLDOUT = PACKAGE / "gold_queries_ru_holdout.json"


class ExpandQueryTests(unittest.TestCase):
    def test_russian_rule_words_append_the_english_section_names(self):
        expanded = expand_query("Я хочу перепрыгнуть через пропасть")
        self.assertTrue(expanded.startswith("Я хочу перепрыгнуть через пропасть"))
        self.assertIn("jump", expanded.split())

    def test_stems_cover_inflections(self):
        for text in ("прыгаю", "перепрыгнуть", "прыжок", "ПРЫГНУЛ"):
            self.assertIn("jump", expand_query(text).split(), text)

    def test_english_and_empty_queries_are_returned_unchanged(self):
        for text in ("how far can I jump", "", "   ", "1d6 damage", "death saving throws"):
            self.assertEqual(expand_query(text), text)

    def test_a_russian_query_without_rule_words_is_unchanged(self):
        self.assertEqual(expand_query("Здравствуйте, расскажите о погоде"), "Здравствуйте, расскажите о погоде")

    def test_several_topics_add_their_terms_once(self):
        words = expand_query("упал и отравлен ядом").split()
        for term in ("falling", "poisoned", "poison"):
            self.assertEqual(words.count(term), 1, term)

    def test_short_stems_need_a_whole_word_and_phrases_need_the_phrase(self):
        self.assertIn("вес", {stem for stems, _ in ENTRIES for stem in stems})
        self.assertIn("lifting", expand_query("какой вес").split())
        self.assertNotIn("lifting", expand_query("наступила весна").split())
        self.assertIn("long", expand_query("мы хотим полный отдых").split())
        self.assertNotIn("long", expand_query("полный провал").split())

    def test_a_four_letter_stem_is_not_a_whole_word_match_so_it_must_not_be_a_common_prefix(self):
        self.assertNotIn("critical", expand_query("критика пришла").split())

    def test_yo_and_ye_are_the_same_letter_in_queries_and_in_stems(self):
        for text in ("герой истощён", "герой истощен", "истощение сил"):
            self.assertIn("exhaustion", expand_query(text).split(), text)
        self.assertIn("darkvision", expand_query("вижу благодаря тёмному зрению").split())


class GlossaryHygieneTests(unittest.TestCase):
    def test_every_entry_has_stems_and_terms(self):
        for stems, terms in ENTRIES:
            self.assertTrue(stems and terms.strip(), (stems, terms))

    def test_stems_are_lowercase_cyrillic_and_not_duplicated(self):
        seen = []
        for stems, _ in ENTRIES:
            for stem in stems:
                self.assertRegex(stem, r"^[а-яё ]+$", stem)
                self.assertEqual(stem, stem.lower())
                seen.append(stem)
        self.assertEqual(len(seen), len(set(seen)), "a stem is listed twice")

    def test_short_stems_are_few_and_deliberate(self):
        short = [stem for stems, _ in ENTRIES for stem in stems if " " not in stem and len(stem) < MIN_STEM_LENGTH]
        self.assertLessEqual(len(short), 8, short)

    @unittest.skipUnless(BOOK_FILE.is_file(), "data/srd.md is not available")
    def test_every_entry_points_at_vocabulary_that_exists_in_the_book(self):
        vocabulary = {token for chunk in split_into_chunks(load_text(BOOK_FILE), 1500) for token in tokenize(chunk.title + " " + chunk.text)}
        for stems, terms in ENTRIES:
            self.assertTrue(set(tokenize(terms)) & vocabulary, f"{stems}: none of {terms!r} occurs in the book")


class LexiconServiceTests(unittest.TestCase):
    def setUp(self):
        self.plain = RulesService.from_text(BOOK)
        self.service = LexiconRulesService(self.plain)

    def test_a_russian_query_finds_the_english_section_that_plain_search_misses(self):
        query = "Я хочу перепрыгнуть через пропасть"
        self.assertEqual(self.plain.search(query), [])
        self.assertEqual(self.service.search(query, 1)[0].title, "Jumping")
        self.assertIn("long jump", self.service.get_rules(query, top_k=1))

    def test_english_queries_give_identical_results(self):
        for query in ("how far can I jump", "fall damage 1d6"):
            self.assertEqual(self.service.search(query), self.plain.search(query))

    def test_exposes_the_chunks_of_the_wrapped_service(self):
        self.assertIs(self.service.chunks, self.plain.chunks)


class BuilderTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.book = Path(self._dir.name) / "book.md"
        self.book.write_text(BOOK, encoding="utf-8")

    def test_keyword_mode_uses_the_glossary_by_default_and_can_switch_it_off(self):
        self.assertIsInstance(build_rules_service(self.book, "keyword"), LexiconRulesService)
        self.assertIsInstance(build_rules_service(self.book, "keyword", lexicon=False), RulesService)

    def test_hybrid_gives_the_glossary_to_its_keyword_half(self):
        cache = Path(self._dir.name) / "cache"
        on = build_rules_service(self.book, "hybrid", cache, embedder=ConceptEmbedder())
        off = build_rules_service(self.book, "hybrid", cache, embedder=ConceptEmbedder(), lexicon=False)
        self.assertIsInstance(on, HybridRulesService)
        self.assertIsInstance(on._keyword, LexiconRulesService)
        self.assertIsInstance(off._keyword, RulesService)


@unittest.skipUnless(BOOK_FILE.is_file(), "data/srd.md is not available")
class MeasuredQualityTests(unittest.TestCase):
    """Floors under the measured numbers in docs/rules_service.md; they catch regressions, they are not targets."""

    def test_holdout_labels_exist_in_the_book(self):
        from rules_service.evaluation import GoldCase

        chunks = split_into_chunks(load_text(BOOK_FILE), 1500)
        for case in load_cases(HOLDOUT):
            for section in case.relevant:
                single = GoldCase(case.query, (section,), case.lang)
                self.assertTrue(any(single.matches(c) for c in chunks), f"{case.query!r}: no section {section!r}")

    def test_holdout_is_disjoint_from_the_main_set(self):
        main = {case.query for case in load_cases()}
        self.assertFalse(main & {case.query for case in load_cases(HOLDOUT)})

    def test_glossary_makes_russian_queries_findable_without_changing_english_ones(self):
        provider = build_rules_service(BOOK_FILE, "keyword")
        plain = build_rules_service(BOOK_FILE, "keyword", lexicon=False)
        seen, holdout = load_cases(), load_cases(HOLDOUT)
        self.assertEqual(evaluate(plain, holdout).recall_at(5), 0.0)
        self.assertGreaterEqual(evaluate(provider, seen).recall_at(5, "ru"), 0.75)
        self.assertGreaterEqual(evaluate(provider, holdout).recall_at(5), 0.55)
        for lang in ("en", "term"):
            self.assertEqual(evaluate(provider, seen).mrr(lang), evaluate(plain, seen).mrr(lang))


if __name__ == "__main__":
    unittest.main()
