"""Reading ``rag_chunks``: the cached lexical index, the access filter and dense search.

The lexical index (BM25 and TF-IDF) is built once from the table and cached
in-process. A cheap fingerprint (row count and newest ``updated_at``) is checked
on every query, so a re-ingest done by the CLI in another process is noticed.
Writers in the API call ``invalidate_cache`` as well, because timestamps can be
coarse.

Dense search is delegated to PostgreSQL (pgvector, ``<=>`` cosine distance) when
the engine is PostgreSQL, and computed in Python otherwise (SQLite in tests).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from app.models.enums import RagBase
from app.models.rag import EMBEDDING_DIM, EmbeddingType, PgVector, RagChunk
from app.services.rag.fusion import cosine
from app.services.rag.lexical import Bm25Index, TfidfIndex

HISTORY_APPROVED = "aprobado"
BASE_FILTERS = ("general", "triage", "all")


@dataclass(frozen=True)
class ChunkMeta:
    chunk_id: str
    doc_id: str
    base: str
    doc_type: str
    estado: str
    patient_pseudonym: str | None
    source: str
    title: str
    url: str | None
    text: str
    word_count: int
    embedded: bool = False


class RagIndex:
    """Lexical index over every chunk; the access filter is applied at query time."""

    def __init__(self, chunks: list[ChunkMeta]) -> None:
        self.chunks = chunks
        self.position = {c.chunk_id: i for i, c in enumerate(chunks)}
        texts = [c.text for c in chunks]
        self.bm25 = Bm25Index(texts)
        self.tfidf = TfidfIndex(texts)

    def get(self, chunk_id: str) -> ChunkMeta:
        return self.chunks[self.position[chunk_id]]


_lock = threading.Lock()
_cache: tuple[tuple[Any, ...], RagIndex] | None = None


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _fingerprint(db: Session) -> tuple[Any, ...]:
    count, newest = db.execute(
        select(func.count(RagChunk.id), func.max(RagChunk.updated_at))
    ).one()
    return (count, newest)


def get_index(db: Session) -> RagIndex:
    global _cache
    fingerprint = _fingerprint(db)
    with _lock:
        if _cache is not None and _cache[0] == fingerprint:
            return _cache[1]
    rows = db.scalars(select(RagChunk).order_by(RagChunk.chunk_id)).all()
    index = RagIndex(
        [
            ChunkMeta(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                base=r.base.value,
                doc_type=r.doc_type,
                estado=r.estado,
                patient_pseudonym=r.patient_pseudonym,
                source=r.source,
                title=r.title,
                url=r.url,
                text=r.text,
                word_count=r.word_count,
                embedded=r.embedding is not None,
            )
            for r in rows
        ]
    )
    with _lock:
        _cache = (fingerprint, index)
    return index


def allowed_chunk_ids(
    index: RagIndex, *, base_filter: str = "all", patient_pseudonym: str | None = None
) -> set[str]:
    """Pre-filter: what a query may even look at.

    Guideline chunks (``general`` / ``triage``) are always allowed, narrowed by
    ``base_filter``. A ``historia`` chunk is allowed only when it is APPROVED and
    belongs to the pseudonym of the patient the question is about. Everything else
    (another patient's history, anything not approved) never reaches scoring.
    """

    if base_filter not in BASE_FILTERS:
        raise ValueError(f"Unknown base filter: {base_filter}")
    allowed: set[str] = set()
    for chunk in index.chunks:
        if chunk.base == RagBase.HISTORY.value:
            if (
                patient_pseudonym is not None
                and chunk.estado == HISTORY_APPROVED
                and chunk.patient_pseudonym == patient_pseudonym
            ):
                allowed.add(chunk.chunk_id)
        elif base_filter in ("all", chunk.base):
            allowed.add(chunk.chunk_id)
    return allowed


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def dense_scores(
    db: Session, query_vector: list[float], allowed: set[str]
) -> dict[str, float]:
    """Cosine similarity of the query to every allowed chunk that has an embedding."""

    if not allowed:
        return {}
    ids = sorted(allowed)
    if _is_postgres(db):
        stmt = dense_statement(query_vector, ids)
        return {chunk_id: float(score) for chunk_id, score in db.execute(stmt)}
    rows = db.execute(
        select(RagChunk.chunk_id, RagChunk.embedding).where(
            RagChunk.chunk_id.in_(ids), RagChunk.embedding.is_not(None)
        )
    )
    return {chunk_id: cosine(query_vector, vector) for chunk_id, vector in rows if vector}


def dense_statement(query_vector: list[float], ids: list[str]) -> sa.Select[Any]:
    """pgvector query: ``1 - (embedding <=> :q)``, nearest first, over the allowed ids."""

    query = sa.cast(sa.bindparam("q", value=query_vector, type_=EmbeddingType()), PgVector(EMBEDDING_DIM))
    distance = RagChunk.embedding.op("<=>", return_type=Float)(query)
    return (
        select(RagChunk.chunk_id, (1 - distance).label("score"))
        .where(RagChunk.chunk_id.in_(ids), RagChunk.embedding.is_not(None))
        .order_by(distance)
    )


def embeddings_for(db: Session, chunk_ids: list[str]) -> dict[str, list[float]]:
    if not chunk_ids:
        return {}
    rows = db.execute(
        select(RagChunk.chunk_id, RagChunk.embedding).where(
            RagChunk.chunk_id.in_(chunk_ids), RagChunk.embedding.is_not(None)
        )
    )
    return {chunk_id: vector for chunk_id, vector in rows if vector}


def base_counts(db: Session) -> dict[str, int]:
    counts = {base.value: 0 for base in RagBase}
    for base, total in db.execute(select(RagChunk.base, func.count()).group_by(RagChunk.base)):
        counts[base.value] = total
    return counts
