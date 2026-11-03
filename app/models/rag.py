"""Clinical RAG storage: knowledge chunks and clinician-reviewed suggestions.

``rag_chunks`` holds the searchable passages. Embeddings use the pgvector
``vector(384)`` type on PostgreSQL and a JSON list on SQLite (unit tests), through
one ``TypeDecorator``; the type is implemented here so the API image does not
need the ``pgvector`` Python package.

``rag_suggestions`` mirrors the AI report life cycle: a suggestion is born
``pendiente``, a doctor or specialist approves or rejects it, and only an approved
one reaches the clinical record and the patient's ``historia`` chunks. The prompt
that was sent is evidence and is written once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    Uuid,
    event,
    func,
    inspect,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import RagAnswerMode, RagBase, RagSuggestionStatus, enum_column
from app.models.governance import JSON_COLUMN
from app.models.identity import User
from app.models.patient import Patient

EMBEDDING_DIM = 384


class PgVector(UserDefinedType):
    """The ``vector(n)`` column type of the pgvector extension."""

    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dim})"


class EmbeddingType(TypeDecorator):
    """``vector(384)`` on PostgreSQL, a JSON list of floats elsewhere.

    The Python value is always ``list[float] | None``. On PostgreSQL the driver
    exchanges pgvector's text form (``[0.1,0.2,...]``), converted here.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PgVector(EMBEDDING_DIM))
        # none_as_null: a missing vector is SQL NULL, so ``IS NULL`` filters work.
        return dialect.type_descriptor(JSON(none_as_null=True))

    def process_bind_param(self, value: list[float] | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return "[" + ",".join(repr(float(x)) for x in value) + "]"
        return [float(x) for x in value]

    def process_result_value(self, value: Any, dialect: Dialect) -> list[float] | None:
        if value is None:
            return None
        if isinstance(value, str):
            body = value.strip()[1:-1]
            return [float(x) for x in body.split(",")] if body else []
        return [float(x) for x in value]


class RagChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rag_chunks"
    __table_args__ = (
        # Approximate nearest-neighbour search by cosine distance. PostgreSQL
        # only; the other dialects ignore the ``postgresql_*`` options.
        Index(
            "ix_rag_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # "<doc_id>-<i>": stable, human readable, and what the evaluation cites.
    chunk_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    base: Mapped[RagBase] = mapped_column(enum_column(RagBase, "rag_base"), nullable=False, index=True)
    doc_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # "vigente" for guidelines; "aprobado" for the patient's approved history.
    estado: Mapped[str] = mapped_column(String(16), nullable=False, default="vigente")
    # Pseudonym (never the real id) of the patient a ``historia`` chunk belongs to.
    patient_pseudonym: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    keywords: Mapped[list[str]] = mapped_column(JSON_COLUMN, nullable=False, default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Lets other processes (the ingest CLI) tell the API that its cache is stale.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<RagChunk {self.chunk_id}>"


SUGGESTION_IMMUTABLE_COLUMNS = (
    "patient_id",
    "question",
    "answer",
    "mode",
    "sources",
    "prompt_sent",
    "model",
    "requested_by",
    "created_at",
)


class RagSuggestion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rag_suggestions"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Already pseudonymized: it is what the model was asked.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[RagAnswerMode] = mapped_column(
        enum_column(RagAnswerMode, "rag_answer_mode"), nullable=False
    )
    # [{n, chunk_id, doc_id, title, url, score}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON_COLUMN, nullable=False)
    # The exact pseudonymized chat messages: evidence of what left the system.
    prompt_sent: Mapped[list[dict[str, Any]]] = mapped_column(JSON_COLUMN, nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[RagSuggestionStatus] = mapped_column(
        enum_column(RagSuggestionStatus, "rag_suggestion_status"),
        nullable=False,
        default=RagSuggestionStatus.PENDING,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])
    requester: Mapped[User] = relationship(foreign_keys=[requested_by])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return f"<RagSuggestion {self.id} {self.status}>"


@event.listens_for(RagSuggestion, "before_update")
def _refuse_changes_to_evidence(_mapper: Mapper, _connection: object, target: RagSuggestion) -> None:
    state = inspect(target)
    changed = [
        name for name in SUGGESTION_IMMUTABLE_COLUMNS if state.attrs[name].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "Una sugerencia RAG es inmutable: no se pueden modificar " + ", ".join(changed)
        )

