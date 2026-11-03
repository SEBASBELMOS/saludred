"""Rank fusion and diversification: RRF and MMR."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

K = TypeVar("K", bound=Hashable)

RRF_K = 60


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf(
    rankings: Sequence[Sequence[K]], k: int = RRF_K, weights: Sequence[float] | None = None
) -> list[tuple[K, float]]:
    """Reciprocal Rank Fusion: ``sum(weight / (k + rank))`` over every ranking.

    With no ``weights`` every ranking counts the same (plain RRF).
    """

    weights = weights if weights is not None else [1.0] * len(rankings)
    total: dict[K, float] = defaultdict(float)
    first_seen: dict[K, int] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for position, key in enumerate(ranking, start=1):
            total[key] += weight / (k + position)
            first_seen.setdefault(key, len(first_seen))
    # Ties keep the order in which a key first appeared (deterministic).
    return sorted(total.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def mmr(
    candidates: Sequence[K],
    relevance: Callable[[K], float],
    similarity: Callable[[K, K], float],
    *,
    k: int,
    lam: float,
) -> list[K]:
    """Maximal Marginal Relevance: ``lam * relevance - (1 - lam) * max similarity to chosen``."""

    chosen: list[K] = []
    remaining = list(candidates)
    while remaining and len(chosen) < k:
        def value(item: K) -> float:
            redundancy = max((similarity(item, other) for other in chosen), default=0.0)
            return lam * relevance(item) - (1 - lam) * redundancy

        best = max(remaining, key=value)
        chosen.append(best)
        remaining.remove(best)
    return chosen
