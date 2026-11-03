"""Ingesting the curated corpus into ``rag_chunks``.

Idempotent: a chunk whose text did not change keeps its embedding, so running the
ingest twice calls the embedding service only for what is new. Embeddings are
computed BEFORE anything is written, so an outage of the ``ml`` service leaves the
table untouched. ``historia`` chunks (approved suggestions) are never touched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.enums import RagBase
from app.models.rag import EMBEDDING_DIM, RagChunk
from app.services import ml_client
from app.services.errors import MlServiceRejectedError, MlServiceUnavailableError
from app.services.rag import store
from app.services.rag.chunking import chunk_document
from app.services.rag.corpus import CURATED_BASES, CorpusDocument

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


@dataclass
class BaseSummary:
    documents: int = 0
    chunks: int = 0
    words: int = 0
    embedded: int = 0

    @property
    def average_words(self) -> float:
        return self.words / self.chunks if self.chunks else 0.0


@dataclass
class IngestSummary:
    per_base: dict[str, BaseSummary] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    removed: int = 0
    embedded_now: int = 0
    history_backfilled: int = 0


def _embed_all(texts: list[str], progress: Callable[[str], None]) -> list[list[float]]:
    vectors: list[list[float]] = []
    client = ml_client.get_client()
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        answer = client.embed(batch)
        got = [[float(x) for x in v] for v in answer["vectors"]]
        if len(got) != len(batch) or any(len(v) != EMBEDDING_DIM for v in got):
            raise ValueError(f"Embedding service returned vectors that are not {EMBEDDING_DIM}-d")
        vectors.extend(got)
        progress(f"  embedded {len(vectors)}/{len(texts)}")
    return vectors


def embed_missing(
    db: Session, chunk_ids: list[str] | None = None, *, history_only: bool = False, commit: bool = True
) -> int:
    """Embed stored chunks that have no vector (approved history saved while ml was down).

    Best effort: if the embedding service is unavailable nothing changes and 0 is
    returned. Returns how many chunks received a vector.
    """

    stmt = select(RagChunk).where(RagChunk.embedding.is_(None))
    if history_only:
        stmt = stmt.where(RagChunk.base == RagBase.HISTORY)
    if chunk_ids is not None:
        stmt = stmt.where(RagChunk.chunk_id.in_(chunk_ids))
    rows = list(db.scalars(stmt))
    if not rows:
        return 0
    try:
        vectors = _embed_all([r.text for r in rows], lambda _msg: None)
    except (MlServiceUnavailableError, MlServiceRejectedError, ValueError, KeyError):
        logger.warning("Could not backfill %d embedding(s): embedding service unavailable", len(rows))
        return 0
    for row, vector in zip(rows, vectors, strict=True):
        row.embedding = vector
    if commit:
        db.commit()
        store.invalidate_cache()
    return len(rows)


def ingest_corpus(
    db: Session,
    documents: list[CorpusDocument],
    *,
    chunking: str = "paragraph",
    reset: bool = False,
    embed: bool = True,
    progress: Callable[[str], None] = lambda _msg: None,
) -> IngestSummary:
    summary = IngestSummary()
    desired: dict[str, tuple[CorpusDocument, int, str, int]] = {}
    for document in documents:
        chunked = chunk_document(document.body, chunking)
        for p_index in chunked.short_paragraphs:
            message = f"{document.id}: paragraph {p_index} has fewer than 15 words"
            summary.warnings.append(message)
            logger.warning(message)
        base = summary.per_base.setdefault(document.base, BaseSummary())
        base.documents += 1
        for chunk in chunked.chunks:
            desired[f"{document.id}-{chunk.index}"] = (document, chunk.index, chunk.text, chunk.word_count)
            base.chunks += 1
            base.words += chunk.word_count

    curated = [RagBase(b) for b in CURATED_BASES]
    if reset:
        summary.removed += db.execute(delete(RagChunk).where(RagChunk.base.in_(curated))).rowcount or 0
    doc_ids = {d.id for d in documents}
    curated_rows = list(db.scalars(select(RagChunk).where(RagChunk.base.in_(curated))))
    existing = {row.chunk_id: row for row in curated_rows if row.doc_id in doc_ids}
    # A document removed from the corpus must stop being searchable. Only curated
    # chunks are considered: a patient's approved history never goes here.
    for row in curated_rows:
        if row.doc_id not in doc_ids:
            db.delete(row)
            summary.removed += 1

    needs_vector = [
        cid
        for cid, (_doc, _i, text, _w) in desired.items()
        if embed and (cid not in existing or existing[cid].text != text or existing[cid].embedding is None)
    ]
    vectors = dict(
        zip(
            needs_vector,
            _embed_all([desired[cid][2] for cid in needs_vector], progress) if needs_vector else [],
            strict=True,
        )
    )

    for cid, row in existing.items():
        if cid not in desired:
            db.delete(row)
            summary.removed += 1
    for cid, (document, _index, text, words) in desired.items():
        row = existing.get(cid)
        if row is None:
            row = RagChunk(chunk_id=cid)
            db.add(row)
        elif row.text != text:
            row.embedding = None
        row.doc_id = document.id
        row.base = RagBase(document.base)
        row.doc_type = document.doc_type
        row.estado = "vigente"
        row.patient_pseudonym = None
        row.source = document.publisher
        row.title = document.title
        row.url = document.url
        row.keywords = document.keywords
        row.text = text
        row.word_count = words
        if cid in vectors:
            row.embedding = vectors[cid]
        if row.embedding is not None:
            summary.per_base[document.base].embedded += 1
    summary.embedded_now = len(vectors)
    if embed:
        summary.history_backfilled = embed_missing(db, history_only=True, commit=False)
    db.commit()
    store.invalidate_cache()
    return summary
