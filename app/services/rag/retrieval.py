"""Retrieval: one entry point used by the API and by the evaluation.

Methods
-------
``tfidf`` / ``bm25``  lexical ranking.
``dense``             cosine over the embeddings of the ``ml`` service.
``hybrid``            Reciprocal Rank Fusion (k=60) of BM25 and dense.
``hybrid_mmr``        the top candidates of ``hybrid`` re-ranked with MMR (production default).

If the embedding service is unreachable the result says so (``degraded``) and the
dense part is simply left out: ``dense`` and ``hybrid`` become BM25, ``hybrid_mmr``
skips the diversification. A query never fails because of it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.services import ml_client
from app.services.errors import MlServiceRejectedError, MlServiceUnavailableError
from app.services.rag import fusion, store
from app.services.rag.ingest import embed_missing
from app.services.rag.text import tokenize
from app.services.rag.store import ChunkMeta

logger = logging.getLogger(__name__)

METHODS = ("tfidf", "bm25", "dense", "hybrid", "hybrid_mmr")
DENSE_METHODS = ("dense", "hybrid", "hybrid_mmr")


@dataclass(frozen=True)
class Hit:
    chunk: ChunkMeta
    score: float


# A degraded (BM25-only) query has no dense check to back its lexical score, so the
# score is normalized by the number of query terms. A typical question has about
# this many content terms, which keeps the threshold comparable to the hybrid one.
DEGRADED_TERM_SCALE = 3.0


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[Hit]
    method: str
    # True when the method wanted embeddings and they were not available.
    degraded: bool
    # Strongest signals over the allowed chunks, used to decide abstention.
    max_cosine: float | None
    max_bm25: float
    # The best BM25 chunk is also among the dense top ``rag_candidates``: a lexical
    # match that the embeddings do not support is usually a stray shared word.
    bm25_top_in_dense_top: bool = False
    query_terms: int = 1

    def has_evidence(self, settings: Settings | None = None, *, extra_bm25: float = 0.0) -> bool:
        """Evidence is a close embedding, or a strong lexical match that embeddings agree with.

        Without embeddings (degraded) the lexical score alone decides, normalized by
        the number of query terms. ``extra_bm25`` (the patient's own summary) is a
        lexical match against text that is not in the index, so it stands alone.
        """

        settings = settings or get_settings()
        if (self.max_cosine or 0.0) >= settings.rag_min_cosine:
            return True
        if extra_bm25 >= settings.rag_min_bm25:
            return True
        if self.max_cosine is None:
            normalized = self.max_bm25 / max(1, self.query_terms)
            return normalized >= settings.rag_min_bm25 / DEGRADED_TERM_SCALE
        return self.max_bm25 >= settings.rag_min_bm25 and self.bm25_top_in_dense_top


def embed_query(question: str) -> list[float] | None:
    """The question's embedding, or ``None`` when the ml service cannot provide it."""

    try:
        answer = ml_client.get_client().embed([question])
        vector = [float(x) for x in answer["vectors"][0]]
    except (MlServiceUnavailableError, MlServiceRejectedError):
        logger.warning("Embeddings unavailable: retrieval degrades to lexical only")
        return None
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("Embedding service returned an unexpected answer")
        return None
    return vector


def _ranked(scores: dict[str, float]) -> list[str]:
    """Chunk ids by descending score, ties by id; zero scores carry no evidence."""

    return [cid for cid, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if s > 0]


def retrieve(
    db: Session,
    question: str,
    *,
    method: str = "hybrid_mmr",
    k: int | None = None,
    base_filter: str = "all",
    patient_pseudonym: str | None = None,
    candidates: int | None = None,
    query_vector: list[float] | None = None,
    use_dense: bool = True,
    dense_weight: float | None = None,
    lexical_weight: float | None = None,
) -> RetrievalResult:
    """Top ``k`` chunks for ``question``.

    ``query_vector`` lets a caller that already embedded the question (the
    evaluation, which runs five methods per question) skip the network call.
    ``use_dense=False`` skips embeddings altogether (lexical methods only).
    ``dense_weight`` / ``lexical_weight`` override the RRF weights for this call.
    """

    if method not in METHODS:
        raise ValueError(f"Unknown retrieval method: {method}")
    settings = get_settings()
    k = k or settings.rag_default_k
    pool_size = max(candidates or settings.rag_candidates, k)

    index = store.get_index(db)
    allowed = store.allowed_chunk_ids(
        index, base_filter=base_filter, patient_pseudonym=patient_pseudonym
    )
    if not allowed:
        return RetrievalResult([], method, False, None, 0.0)

    bm25_all = index.bm25.scores(question)
    bm25_scores = {cid: bm25_all[index.position[cid]] for cid in allowed}

    dense: dict[str, float] = {}
    vector = query_vector
    if use_dense and vector is None:
        vector = embed_query(question)
    if use_dense and vector is not None:
        missing = [
            cid for cid in allowed if not index.get(cid).embedded and index.get(cid).base == "historia"
        ]
        if missing:
            embed_missing(db, missing)  # best effort; vectors are read just below
        dense = store.dense_scores(db, vector, allowed)
    wants_dense = method in DENSE_METHODS
    degraded = wants_dense and not dense
    floor = settings.rag_dense_floor
    dense_ranking = [c for c in sorted(dense, key=lambda c: (-dense[c], c)) if dense[c] >= floor]

    scored: list[tuple[str, float]]
    if method == "tfidf":
        tfidf_all = index.tfidf.scores(question)
        tfidf_scores = {cid: tfidf_all[index.position[cid]] for cid in allowed}
        scored = [(cid, tfidf_scores[cid]) for cid in _ranked(tfidf_scores)]
    elif method == "bm25" or (method == "dense" and degraded):
        scored = [(cid, bm25_scores[cid]) for cid in _ranked(bm25_scores)]
    elif method == "dense":
        scored = [(cid, dense[cid]) for cid in dense_ranking]
    else:  # hybrid, hybrid_mmr
        rankings = [_ranked(bm25_scores)]
        weights = [settings.rag_rrf_lexical_weight if lexical_weight is None else lexical_weight]
        if dense:
            rankings.append(dense_ranking)
            weights.append(settings.rag_rrf_dense_weight if dense_weight is None else dense_weight)
        scored = fusion.rrf(rankings, weights=weights)
        if dense:
            # Weak on both signals is noise that RRF would still rank.
            scored = [
                (cid, score)
                for cid, score in scored
                if dense.get(cid, 0.0) >= floor or bm25_scores[cid] >= settings.rag_min_bm25
            ]
        if method == "hybrid_mmr" and dense:
            pool = [cid for cid, _ in scored[:pool_size]]
            vectors = store.embeddings_for(db, pool)
            fused = dict(scored)
            top_fused = max(fused.values(), default=1.0) or 1.0

            def relevance(cid: str) -> float:
                # No dense vector (history saved while ml was down): its normalized
                # RRF score stands in, instead of a 0 that would bury it.
                return dense[cid] if cid in dense else fused[cid] / top_fused

            def similarity(a: str, b: str) -> float:
                if a in vectors and b in vectors:
                    return fusion.cosine(vectors[a], vectors[b])
                return 0.0

            order = fusion.mmr(pool, relevance, similarity, k=k, lam=settings.rag_mmr_lambda)
            scored = [(cid, fused[cid]) for cid in order]

    hits = [Hit(index.get(cid), float(score)) for cid, score in scored[:k]]
    top_bm25 = max(bm25_scores, key=lambda c: (bm25_scores[c], c)) if bm25_scores else None
    dense_top = set(sorted(dense, key=lambda c: (-dense[c], c))[: max(1, settings.rag_candidates)])
    return RetrievalResult(
        hits=hits,
        method=method,
        degraded=degraded,
        max_cosine=max(dense.values()) if dense else None,
        max_bm25=max(bm25_scores.values(), default=0.0),
        bm25_top_in_dense_top=top_bm25 in dense_top if dense else False,
        query_terms=len(tokenize(question)),
    )
