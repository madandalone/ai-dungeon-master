import tempfile
import unittest
from pathlib import Path

from engine.dice import Dice, SequenceSource
from integration import IntegrationConfigError, IntegrationError, RulesSettings, build_orchestrator, build_rules_provider
from integration.tests.test_orchestrator import BOOK, FakeDM, make_action, make_state, response
from rules_service import HybridRulesService, RagRulesService, RulesService, RulesUnavailableError
from rules_service.tests.test_rag import ConceptEmbedder


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        settings = RulesSettings.from_env({"RULES_BOOK_PATH": "data/srd.md"})
        self.assertEqual(settings.book_path, Path("data/srd.md"))
        self.assertEqual(settings.mode, "keyword")
        self.assertEqual(settings.cache_dir, Path(".rules_index"))
        self.assertIsNone(settings.embedding_model)

    def test_all_values_and_mode_normalisation(self):
        settings = RulesSettings.from_env({
            "RULES_BOOK_PATH": "b.md", "RULES_MODE": " Hybrid ", "RULES_CACHE_DIR": "c", "RULES_EMBEDDING_MODEL": "m",
        })
        self.assertEqual((settings.mode, settings.cache_dir, settings.embedding_model), ("hybrid", Path("c"), "m"))

    def test_missing_book_path_is_a_config_error(self):
        with self.assertRaises(IntegrationConfigError):
            RulesSettings.from_env({})

    def test_unknown_mode_is_a_config_error(self):
        with self.assertRaises(IntegrationConfigError):
            RulesSettings.from_env({"RULES_BOOK_PATH": "b.md", "RULES_MODE": "magic"})

    def test_config_error_is_not_a_turn_error(self):
        self.assertFalse(issubclass(IntegrationConfigError, IntegrationError))


class BuildRulesProviderTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.book = Path(self._dir.name) / "book.md"
        self.book.write_text(BOOK, encoding="utf-8")
        self.cache = Path(self._dir.name) / "cache"

    def settings(self, mode):
        return RulesSettings(self.book, mode, self.cache)

    def test_each_mode_builds_the_matching_provider(self):
        embedder = ConceptEmbedder()
        self.assertIsInstance(build_rules_provider(self.settings("keyword")), RulesService)
        self.assertIsInstance(build_rules_provider(self.settings("rag"), embedder), RagRulesService)
        self.assertIsInstance(build_rules_provider(self.settings("hybrid"), embedder), HybridRulesService)

    def test_keyword_mode_needs_no_embedder_or_cache(self):
        build_rules_provider(self.settings("keyword"))
        self.assertFalse(self.cache.exists())

    def test_rag_mode_writes_the_index_cache(self):
        build_rules_provider(self.settings("rag"), ConceptEmbedder())
        self.assertEqual(len(list(self.cache.glob("*.npy"))), 1)

    def test_missing_model_fails_fast_at_startup_even_with_a_warm_cache(self):
        class MissingModel:
            name = "missing"

            def encode(self, texts):
                raise RulesUnavailableError("no weights")

        for mode in ("rag", "hybrid"):
            with self.subTest(mode=mode), self.assertRaises(IntegrationConfigError) as caught:
                build_rules_provider(self.settings(mode), MissingModel())
            self.assertIn(f"RULES_MODE={mode}", str(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, RulesUnavailableError)

    def test_missing_book_file_is_a_config_error(self):
        with self.assertRaises(IntegrationConfigError):
            build_rules_provider(RulesSettings(self.book.with_name("nope.md")))


class BuildOrchestratorTests(unittest.TestCase):
    def test_wires_rules_into_a_full_turn_with_injected_dm(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "book.md"
            book.write_text(BOOK, encoding="utf-8")
            dm = FakeDM(response())
            orchestrator = build_orchestrator(RulesSettings(book), dm=dm)
            outcome = orchestrator.play_turn(make_state(), make_action(), "plot", Dice(SequenceSource([10])))
        self.assertTrue(outcome.accepted)
        self.assertIn("long jump", dm.requests[0].rules_summary)

    def test_explicit_rules_skip_the_book_entirely(self):
        class Stub:
            def get_rules(self, query):
                return "stub rule"

        dm = FakeDM(response())
        build_orchestrator(dm=dm, rules=Stub()).play_turn(make_state(), make_action(), "plot", Dice(SequenceSource([10])))
        self.assertEqual(dm.requests[0].rules_summary, "stub rule")


if __name__ == "__main__":
    unittest.main()
