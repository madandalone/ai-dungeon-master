import unittest

import numpy as np

from rules_service import HybridRulesService, RagRulesService, RulesService, RulesUnavailableError
from rules_service.embedder import SentenceTransformerEmbedder
from rules_service.tests.test_rag import BOOK, ConceptEmbedder


class FlakyEmbedder(ConceptEmbedder):
    """Works while building the index, then the model becomes unavailable."""

    def __init__(self):
        super().__init__()
        self.down = False

    def encode(self, texts):
        if self.down:
            raise RulesUnavailableError("model went away")
        return super().encode(texts)


def failing_loader(error):
    def load(model_name):
        raise error

    return load


class EmbedderLoadTests(unittest.TestCase):
    def test_any_loader_failure_becomes_rules_unavailable_with_the_cause_kept(self):
        for loader_error in (OSError("offline"), ImportError("no package"), AttributeError("partial cache")):
            with self.subTest(error=type(loader_error).__name__):
                embedder = SentenceTransformerEmbedder("some-model", loader=failing_loader(loader_error))
                with self.assertRaises(RulesUnavailableError) as caught:
                    embedder.encode(["x"])
                self.assertIs(caught.exception.__cause__, loader_error)
                self.assertIn("some-model", str(caught.exception))
                self.assertIn(type(loader_error).__name__, str(caught.exception))

    def test_a_failed_load_is_retried_not_cached(self):
        attempts = []

        class Model:
            def encode(self, texts, **kwargs):
                return np.ones((len(texts), 2))

        def flaky_loader(model_name):
            attempts.append(model_name)
            if len(attempts) == 1:
                raise OSError("offline")
            return Model()

        embedder = SentenceTransformerEmbedder("some-model", loader=flaky_loader)
        with self.assertRaises(RulesUnavailableError):
            embedder.encode(["x"])
        self.assertEqual(embedder.encode(["x"]).shape, (1, 2))
        self.assertEqual(len(attempts), 2)

    def test_loaded_model_vectors_are_returned_as_float32(self):
        class Model:
            def encode(self, texts, **kwargs):
                return np.ones((len(texts), 3), dtype=np.float64)

        vectors = SentenceTransformerEmbedder("m", loader=lambda name: Model()).encode(["a", "b"])
        self.assertEqual((vectors.shape, vectors.dtype), ((2, 3), np.float32))


class HybridDegradationTests(unittest.TestCase):
    def setUp(self):
        self.embedder = FlakyEmbedder()
        self.keyword = RulesService.from_text(BOOK)
        self.service = HybridRulesService(self.keyword, RagRulesService.from_text(BOOK, self.embedder))

    def test_falls_back_to_keyword_results_when_semantic_search_is_unavailable(self):
        self.embedder.down = True
        with self.assertLogs("rules_service.hybrid", "WARNING"):
            results = self.service.search("1d6 bludgeoning damage", top_k=1)
        self.assertEqual(results, self.keyword.search("1d6 bludgeoning damage", top_k=1))

    def test_pure_rag_still_reports_unavailability_to_the_caller(self):
        rag = RagRulesService.from_text(BOOK, self.embedder)
        self.embedder.down = True
        with self.assertRaises(RulesUnavailableError):
            rag.get_rules("jump")

    def test_other_errors_are_not_swallowed_by_the_fallback(self):
        class Buggy(FlakyEmbedder):
            def encode(self, texts):
                if self.down:
                    raise ValueError("a bug")
                return super().encode(texts)

        buggy = Buggy()
        service = HybridRulesService(RulesService.from_text(BOOK), RagRulesService.from_text(BOOK, buggy))
        buggy.down = True
        with self.assertRaises(ValueError):
            service.search("jump")


if __name__ == "__main__":
    unittest.main()
