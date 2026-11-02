import json
import tempfile
import unittest
from pathlib import Path

from rules_service import RulesService
from rules_service.evaluation import GoldCase, evaluate, format_report, load_cases
from rules_service.service import RuleChunk, load_text, split_into_chunks

BOOK_FILE = Path(__file__).resolve().parents[2] / "data" / "srd.md"


def chunk(path):
    return RuleChunk(title=path.split(" > ")[-1], text="text", path=path)


class FakeProvider:
    def __init__(self, ranked):
        self.ranked = ranked
        self.top_ks = []

    def search(self, query, top_k=3):
        self.top_ks.append(top_k)
        return [chunk(p) for p in self.ranked[query]][:top_k]


class MatchTests(unittest.TestCase):
    case = GoldCase("q", ("Movement > Jumping",))

    def test_matches_the_section_its_ancestors_and_its_descendants(self):
        for path in ("Book > Movement > Jumping", "Movement > Jumping", "Book > Movement > Jumping > Long Jump"):
            self.assertTrue(self.case.matches(chunk(path)), path)

    def test_does_not_match_on_partial_names(self):
        for path in ("Book > Magic Items > Ring of Jumping", "Book > Movement > Jumping Jacks", "Book > Movement"):
            self.assertFalse(self.case.matches(chunk(path)), path)

    def test_any_of_several_relevant_sections_counts(self):
        case = GoldCase("q", ("A > X", "B > Y"))
        self.assertTrue(case.matches(chunk("Book > B > Y")))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            GoldCase("first", ("S > One",), "ru"),
            GoldCase("third", ("S > Two",), "ru"),
            GoldCase("absent", ("S > Three",), "en"),
            GoldCase("beyond", ("S > Four",), "en"),
        ]
        self.provider = FakeProvider({
            "first": ["S > One", "S > X"],
            "third": ["S > X", "S > Y", "S > Two"],
            "absent": ["S > X", "S > Y"],
            "beyond": ["S > X", "S > Y", "S > Z", "S > Four"],
        })

    def test_ranks_are_one_based_and_none_when_missing(self):
        report = evaluate(self.provider, self.cases, max_k=3)
        self.assertEqual([r.rank for r in report.results], [1, 3, None, None])

    def test_recall_and_mrr_overall_and_per_language(self):
        report = evaluate(self.provider, self.cases, max_k=5)
        self.assertEqual([r.rank for r in report.results], [1, 3, None, 4])
        self.assertEqual(report.recall_at(1), 0.25)
        self.assertEqual(report.recall_at(3), 0.5)
        self.assertEqual(report.recall_at(5), 0.75)
        self.assertAlmostEqual(report.mrr(), (1 + 1 / 3 + 0 + 1 / 4) / 4)
        self.assertEqual(report.recall_at(3, "ru"), 1.0)
        self.assertEqual(report.recall_at(3, "en"), 0.0)

    def test_provider_is_asked_for_exactly_max_k_results(self):
        evaluate(self.provider, self.cases, max_k=7)
        self.assertEqual(set(self.provider.top_ks), {7})

    def test_empty_inputs_do_not_divide_by_zero(self):
        report = evaluate(self.provider, [], max_k=3)
        self.assertEqual((report.recall_at(3), report.mrr(), report.recall_at(3, "ru")), (0.0, 0.0, 0.0))

    def test_misses_lists_cases_outside_the_cutoff(self):
        report = evaluate(self.provider, self.cases, max_k=5)
        self.assertEqual([r.case.query for r in report.misses(3)], ["absent", "beyond"])

    def test_report_text_has_a_row_per_language_and_lists_misses(self):
        text = format_report(evaluate(self.provider, self.cases, max_k=5), ks=(1, 3, 5))
        for part in ("all", "en", "ru", "R@1", "R@3", "R@5", "MRR", "[en] absent"):
            self.assertIn(part, text)
        self.assertNotIn("[ru] first", text)


class GoldSetTests(unittest.TestCase):
    def test_load_cases_reads_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            path.write_text(json.dumps([{"query": "q", "relevant": ["A > B"], "lang": "ru"}, {"query": "p", "relevant": ["C"]}]))
            cases = load_cases(path)
        self.assertEqual(cases, [GoldCase("q", ("A > B",), "ru"), GoldCase("p", ("C",), "en")])

    def test_shipped_gold_set_is_well_formed(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 40)
        self.assertEqual(len({c.query for c in cases}), len(cases), "duplicate queries")
        self.assertTrue(all(c.relevant and all(c.relevant) for c in cases))
        self.assertEqual({c.lang for c in cases}, {"ru", "en", "term"})

    @unittest.skipUnless(BOOK_FILE.is_file(), "data/srd.md is not available")
    def test_every_label_points_at_a_real_section_of_the_book(self):
        chunks = split_into_chunks(load_text(BOOK_FILE), 500)
        for case in load_cases():
            for section in case.relevant:
                single = GoldCase(case.query, (section,), case.lang)
                self.assertTrue(any(single.matches(c) for c in chunks), f"{case.query!r}: no section {section!r}")

    @unittest.skipUnless(BOOK_FILE.is_file(), "data/srd.md is not available")
    def test_keyword_baseline_is_blind_to_russian_but_finds_exact_terms(self):
        report = evaluate(RulesService.from_file(BOOK_FILE), load_cases())
        self.assertEqual(report.recall_at(5, "ru"), 0.0)
        self.assertGreaterEqual(report.recall_at(5, "term"), 0.85)
        self.assertGreaterEqual(report.recall_at(5, "en"), 0.95)
        self.assertGreaterEqual(report.recall_at(1, "en"), 0.75)
        self.assertGreaterEqual(report.mrr("en"), 0.8)


if __name__ == "__main__":
    unittest.main()
