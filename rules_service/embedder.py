from typing import Protocol

import numpy as np

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized float32 vectors of shape (len(texts), dim)."""


class SentenceTransformerEmbedder:
    """Multilingual local model; loaded on first use so imports stay cheap."""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.name = model_name
        self._model = None

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.name)
        vectors = self._model.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.astype(np.float32)
