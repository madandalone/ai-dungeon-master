import hashlib
from pathlib import Path

import numpy as np

from rules_service.embedder import Embedder
from rules_service.service import RuleChunk, format_rules, load_text, split_into_chunks

RAG_CHUNK_CHARS = 500  # the embedding model truncates input at ~128 tokens
INDEX_VERSION = "1"


def _chunk_text(chunk: RuleChunk) -> str:
    return f"{chunk.path}\n{chunk.text}" if chunk.path else chunk.text


class RagRulesService:
    """Semantic retrieval: chunks are embedded once, queries are matched by cosine similarity."""

    def __init__(self, chunks: list[RuleChunk], embedder: Embedder, vectors: np.ndarray | None = None):
        self.chunks = chunks
        self._embedder = embedder
        self._vectors = vectors if vectors is not None else self._embed(chunks)

    def _embed(self, chunks: list[RuleChunk]) -> np.ndarray:
        if not chunks:
            return np.zeros((0, 0), dtype=np.float32)
        return self._embedder.encode([_chunk_text(c) for c in chunks])

    @classmethod
    def from_text(
        cls,
        text: str,
        embedder: Embedder,
        cache_dir: str | Path | None = None,
        max_chars: int = RAG_CHUNK_CHARS,
    ) -> "RagRulesService":
        chunks = split_into_chunks(text, max_chars)
        if cache_dir is None:
            return cls(chunks, embedder)
        key = hashlib.sha256(f"{INDEX_VERSION}|{embedder.name}|{max_chars}|{text}".encode()).hexdigest()[:16]
        cache_file = Path(cache_dir) / f"{key}.npy"
        if cache_file.exists():
            vectors = np.load(cache_file)
            if len(vectors) == len(chunks):
                return cls(chunks, embedder, vectors)
        service = cls(chunks, embedder)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_file, service._vectors)
        return service

    @classmethod
    def from_file(cls, path: str | Path, embedder: Embedder, cache_dir: str | Path | None = None) -> "RagRulesService":
        return cls.from_text(load_text(path), embedder, cache_dir)

    def search(self, query: str, top_k: int = 3, min_score: float = 0.0) -> list[RuleChunk]:
        if not self.chunks or not query.strip():
            return []
        scores = self._vectors @ self._embedder.encode([query])[0]
        order = np.argsort(-scores)[:top_k]
        return [self.chunks[i] for i in order if scores[i] >= min_score]

    def get_rules(self, query: str, top_k: int = 3, max_chars: int = 4000, min_score: float = 0.0) -> str:
        return format_rules(self.search(query, top_k, min_score), max_chars)
