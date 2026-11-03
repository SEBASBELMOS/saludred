"""clinical RAG: pgvector extension and the rag_chunks table

Revision ID: b8d3f5a1c492
Revises: a7c4e9d2b815
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision: str = "b8d3f5a1c492"
down_revision: str | None = "a7c4e9d2b815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASES = ("general", "triage", "historia")
EMBEDDING_DIM = 384


class Vector(UserDefinedType):
    """pgvector's ``vector(n)``; declared here so the migration needs no extra package."""

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: object) -> str:
        return f"vector({self.dim})"


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        # Requires pgvector: the image pgvector/pgvector:pg16 locally, or a managed
        # PostgreSQL that offers it (Neon does; the owner role may create it).
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "No se pudo crear la extension pgvector (CREATE EXTENSION vector). "
                "Use la imagen pgvector/pgvector:pg16 o un PostgreSQL gestionado que la ofrezca "
                "(en Neon el rol propietario puede crearla), o pida a un superusuario ejecutar "
                "'CREATE EXTENSION vector;' en esta base y repita la migracion. Ver docs/RAG.md."
            ) from exc

    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.String(length=80), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("base", sa.String(length=32), nullable=False),
        sa.Column("doc_type", sa.String(length=40), nullable=False),
        sa.Column("estado", sa.String(length=16), nullable=False),
        sa.Column("patient_pseudonym", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=600), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column(
            "embedding",
            Vector(EMBEDDING_DIM) if is_postgres else sa.JSON(),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_chunks")),
        sa.UniqueConstraint("chunk_id", name=op.f("uq_rag_chunks_chunk_id")),
    )
    # Same procedure as the audit actions: the name goes without the ck_<table>_ prefix.
    op.create_check_constraint("rag_base", "rag_chunks", sa.column("base").in_(BASES))
    op.create_index(op.f("ix_rag_chunks_doc_id"), "rag_chunks", ["doc_id"], unique=False)
    op.create_index(op.f("ix_rag_chunks_base"), "rag_chunks", ["base"], unique=False)
    op.create_index(
        op.f("ix_rag_chunks_patient_pseudonym"), "rag_chunks", ["patient_pseudonym"], unique=False
    )
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_rag_chunks_embedding_hnsw ON rag_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_table("rag_chunks")
    # The extension is left installed on purpose: other objects may use it.
