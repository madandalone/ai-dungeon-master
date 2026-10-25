from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from rules_service.errors import RulesUnavailableError

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized float32 vectors of shape (len(texts), dim)."""


def _load_sentence_transformer(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


class SentenceTransformerEmbedder:
    """Multilingual local model; loaded on first use so imports stay cheap.

    `loader` builds the model from its name (injectable so tests never import torch).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, loader: Callable[[str], Any] = _load_sentence_transformer):
        self.name = model_name
        self._loader = loader
        self._model = None

    def _load(self):
        # The only deliberately broad catch: loaders raise unrelated types for a missing or partial model
        # (OSError offline, ImportError without the package, AttributeError from transformers on a partial cache).
        try:
            return self._loader(self.name)
        except Exception as exc:
            raise RulesUnavailableError(f"Embedding model {self.name!r} could not be loaded ({type(exc).__name__})") from exc

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            self._model = self._load()
        vectors = self._model.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.astype(np.float32)
