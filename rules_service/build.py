from pathlib import Path

from rules_service.embedder import DEFAULT_MODEL, Embedder, SentenceTransformerEmbedder
from rules_service.hybrid import HybridRulesService
from rules_service.rag import RagRulesService
from rules_service.service import RulesService, load_text

MODES = ("keyword", "rag", "hybrid")
DEFAULT_CACHE_DIR = Path(".rules_index")


def build_rules_service(
    book_path: str | Path,
    mode: str = "keyword",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    model: str | None = None,
    embedder: Embedder | None = None,
) -> RulesService | RagRulesService | HybridRulesService:
    """Build the retriever for one mode. Raises RulesUnavailableError if rag/hybrid cannot load the model.

    rag/hybrid warm the model up first: a warm index cache would otherwise postpone a missing model to the first query.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    text = load_text(book_path)
    keyword = RulesService.from_text(text)
    if mode == "keyword":
        return keyword
    embedder = embedder or SentenceTransformerEmbedder(model or DEFAULT_MODEL)
    embedder.encode(["warm-up"])
    rag = RagRulesService.from_text(text, embedder, cache_dir)
    return rag if mode == "rag" else HybridRulesService(keyword, rag)

