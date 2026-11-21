"""audit log hash chain: seq, prev_hash and row_hash

ADDITIVE: three nullable columns and a partial unique index. No existing row is
rewritten or hashed: the chain starts at the first row written after this migration
(``seq`` = 1). Rows written before it keep NULL in the three columns.

``seq`` is NOT an identity column on purpose. Adding an identity (or a sequence
default) to a populated table would number, and so rewrite, every old row. The
application computes ``seq`` under a PostgreSQL advisory lock (``trail.audit`` ->
``app/services/audit_chain.py``), which also serializes the writers so the chain
cannot fork.

Downgrade drops the three columns and the index (the hashes are lost, nothing else).

Revision ID: f1d6c4a8b250
Revises: e8b3a5c7d921
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1d6c4a8b250"
down_revision: str | None = "e8b3a5c7d921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_audit_log_seq"


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_log", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_log", sa.Column("row_hash", sa.String(length=64), nullable=True))
    op.create_index(
        INDEX,
        "audit_log",
        ["seq"],
        unique=True,
        postgresql_where=sa.text("seq IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="audit_log")
    op.drop_column("audit_log", "row_hash")
    op.drop_column("audit_log", "prev_hash")
    op.drop_column("audit_log", "seq")
