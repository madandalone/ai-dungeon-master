import logging
from collections import defaultdict

from rules_service.errors import RulesUnavailableError
from rules_service.rag import RagRulesService
from rules_service.service import RuleChunk, RulesService, format_rules

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60
CANDIDATE_MULTIPLIER = 3


class HybridRulesService:
    """Rank fusion of keyword (BM25) and semantic (RAG) retrieval.

    Keyword search is strong for exact terms like "1d6" or named spells.
    RAG is strong for cross-language and paraphrase queries.
    RRF (Reciprocal Rank Fusion) merges both lists without training weights.
    """

    def __init__(self, keyword: RulesService, rag: RagRulesService, k: int = DEFAULT_RRF_K):
        self._keyword = keyword
        self._rag = rag
        self._k = k

    @staticmethod
    def _chunk_id(chunk: RuleChunk) -> str:
        return f"{chunk.path}\n{chunk.title}\n{chunk.text}"

    def _candidates(self, query: str, top_k: int) -> tuple[dict[str, float], dict[str, RuleChunk]]:
        need = max(top_k * CANDIDATE_MULTIPLIER, 10)
        scores: dict[str, float] = defaultdict(float)
        by_id: dict[str, RuleChunk] = {}

        for rank, chunk in enumerate(self._keyword.search(query, top_k=need), start=1):
            cid = self._chunk_id(chunk)
            scores[cid] += 1.0 / (self._k + rank)
            by_id[cid] = chunk

        try:
            semantic = self._rag.search(query, top_k=need)
        except RulesUnavailableError:
            logger.warning("semantic search unavailable: hybrid search falls back to keyword results", exc_info=True)
            semantic = []
        for rank, chunk in enumerate(semantic, start=1):
            cid = self._chunk_id(chunk)
            scores[cid] += 1.0 / (self._k + rank)
            by_id[cid] = chunk

        return scores, by_id

    def search(self, query: str, top_k: int = 3) -> list[RuleChunk]:
        scores, by_id = self._candidates(query, top_k)
        ordered = sorted(scores, key=scores.get, reverse=True)
        return [by_id[cid] for cid in ordered[:top_k]]

    def get_rules(self, query: str, top_k: int = 3, max_chars: int = 4000) -> str:
        return format_rules(self.search(query, top_k), max_chars)
