"""Sentence embeddings for the clinical RAG (paraphrase-multilingual-MiniLM-L12-v2).

The model is loaded on the first request, not at import time: the service starts
(and answers ``/health``) in seconds, and the API/tests that never ask for an
embedding never pay for torch. The Docker image downloads the weights at build
time, so no internet is needed at run time.
"""

from __future__ import annotations

import os
import threading
from typing import Any

MODEL_NAME = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
DIMENSION = 384


class Embedder:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
            return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """One L2-normalized vector per text, so cosine similarity is a dot product."""

        vectors = self._load().encode(texts, normalize_embeddings=True, batch_size=32)
        return [[float(x) for x in vector] for vector in vectors]
