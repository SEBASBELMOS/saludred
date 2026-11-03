"""BM25 and TF-IDF over an in-memory corpus, in pure Python.

BM25 follows the notebook: ``k1=1.2``, ``b=0.75`` and
``idf = ln(1 + (N - n + 0.5) / (n + 0.5))``. TF-IDF uses the same weighting as
scikit-learn's default vectorizer (smoothed idf, L2-normalized rows), so the
dot product of two vectors is their cosine.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from app.services.rag.text import tokenize


class Bm25Index:
    def __init__(self, documents: Sequence[str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.tokens = [tokenize(d) for d in documents]
        self.size = len(self.tokens)
        self.avg_len = (sum(len(t) for t in self.tokens) / self.size) if self.size else 0.0
        self.tf = [Counter(t) for t in self.tokens]
        df: Counter[str] = Counter(term for t in self.tokens for term in set(t))
        self.idf = {
            term: math.log(1 + (self.size - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def _score(self, query: Sequence[str], tf: Counter[str], length: int) -> float:
        if not self.avg_len:
            return 0.0
        norm = self.k1 * (1 - self.b + self.b * length / self.avg_len)
        total = 0.0
        for term in query:
            freq = tf.get(term, 0)
            if freq:
                total += self.idf[term] * freq * (self.k1 + 1) / (freq + norm)
        return total

    def scores(self, question: str) -> list[float]:
        query = tokenize(question)
        return [
            self._score(query, tf, len(tokens)) for tf, tokens in zip(self.tf, self.tokens, strict=True)
        ]

    def score_text(self, question: str, text: str) -> float:
        """BM25 of an ad-hoc text against the question, using this index's statistics."""

        tokens = tokenize(text)
        return self._score(tokenize(question), Counter(tokens), len(tokens))


class TfidfIndex:
    def __init__(self, documents: Sequence[str]) -> None:
        tokens = [tokenize(d) for d in documents]
        size = len(tokens)
        df: Counter[str] = Counter(term for t in tokens for term in set(t))
        self.idf = {term: math.log((1 + size) / (1 + n)) + 1.0 for term, n in df.items()}
        self.vectors = [self._vector(t) for t in tokens]

    def _vector(self, tokens: Sequence[str]) -> dict[str, float]:
        weights = {
            term: freq * self.idf[term] for term, freq in Counter(tokens).items() if term in self.idf
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        return {term: w / norm for term, w in weights.items()} if norm else {}

    def scores(self, question: str) -> list[float]:
        query = self._vector(tokenize(question))
        if not query:
            return [0.0] * len(self.vectors)
        return [sum(w * vec.get(term, 0.0) for term, w in query.items()) for vec in self.vectors]
