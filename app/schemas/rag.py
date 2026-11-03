"""Pydantic models of the clinical RAG endpoints.

Field names follow the public contract where the course fixes them (``estado``,
``observaciones``, ``motivo``); everything else is English.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import RagAnswerMode, RagSuggestionStatus


class RagAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=500)
    patient_id: uuid.UUID | None = Field(
        default=None,
        description="Patient the question is about. Adds their pseudonymized summary and "
        "approved history, and leaves a suggestion pending clinician review",
    )
    base: Literal["general", "triage", "all"] = "all"
    method: Literal["tfidf", "bm25", "dense", "hybrid", "hybrid_mmr"] = "hybrid"
    k: int = Field(default=3, ge=1, le=10, description="Fragments cited in the prompt")

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("question must not be blank")
        return value


class RagSource(BaseModel):
    n: int
    chunk_id: str
    doc_id: str
    title: str
    url: str | None
    score: float | None


class RagAnswer(BaseModel):
    answer: str
    mode: RagAnswerMode
    sources: list[RagSource]
    suggestion_id: uuid.UUID | None = Field(
        description="Pending suggestion created for a patient question; null otherwise"
    )
    retrieval_degraded: bool = Field(
        description="True when embeddings were unavailable and retrieval was lexical only"
    )
    degraded_reason: str | None = Field(
        default=None,
        description="Why no model answer was produced: llm_not_configured, llm_rate_limited, "
        "llm_timeout or llm_unavailable. Null when the model answered or on abstention",
    )


class RagSuggestionApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observaciones: str | None = Field(
        default=None,
        max_length=1000,
        description="Clinical concept of the signer. Optional unless REQUIRE_REVIEW_NOTE is on",
    )


class RagSuggestionReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=3, max_length=500)
    observaciones: str | None = Field(default=None, max_length=1000)

    @field_validator("motivo")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("motivo must not be blank")
        return value


class RagSuggestionRead(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    question: str
    answer: str
    mode: RagAnswerMode
    sources: list[RagSource]
    model: str
    estado: RagSuggestionStatus
    requested_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    firmado_por: str | None = Field(description="Name of the clinician who signed")
    reviewed_at: datetime | None
    observaciones: str | None
    motivo_rechazo: str | None
    created_at: datetime


class RagSuggestionDetail(RagSuggestionRead):
    prompt_sent: list[dict[str, Any]] = Field(
        description="The exact pseudonymized messages sent to the language model. "
        "Empty unless mode is llm: an extractive answer sent nothing"
    )


class RagStats(BaseModel):
    chunks: dict[str, int]
    total_chunks: int
    embedded_chunks: int
    embedding_model: str
    embedding_dim: int
    llm_model: str
    has_llm_key: bool
    min_cosine: float
    min_bm25: float
    mmr_lambda: float
    candidates: int


class ClinicalRecordSuggestion(BaseModel):
    """An approved RAG suggestion as it appears in the clinical record."""

    id: uuid.UUID
    question: str
    answer: str
    sources: list[RagSource]
    model: str
    estado: RagSuggestionStatus
    observaciones: str | None
    firmado_por: str | None
    reviewed_at: datetime | None
    created_at: datetime
