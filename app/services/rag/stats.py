"""Numbers for the RAG demo panel: what is indexed and how it is configured."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.rag import EMBEDDING_DIM, RagChunk
from app.schemas.rag import RagStats
from app.services.rag import llm, store

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def build_stats(db: Session) -> RagStats:
    settings = get_settings()
    counts = store.base_counts(db)
    embedded = db.scalar(select(func.count()).select_from(RagChunk).where(RagChunk.embedding.is_not(None)))
    client = llm.get_client()
    return RagStats(
        chunks=counts,
        total_chunks=sum(counts.values()),
        embedded_chunks=embedded or 0,
        embedding_model=EMBEDDING_MODEL,
        embedding_dim=EMBEDDING_DIM,
        llm_model=client.model if client is not None else settings.groq_model,
        has_llm_key=client is not None,
        min_cosine=settings.rag_min_cosine,
        min_bm25=settings.rag_min_bm25,
        mmr_lambda=settings.rag_mmr_lambda,
        candidates=settings.rag_candidates,
    )
