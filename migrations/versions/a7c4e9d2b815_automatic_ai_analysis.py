"""automatic AI analysis: origin, encounter and a unique report per encounter

Revision ID: a7c4e9d2b815
Revises: f6b3d8e1a742
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e9d2b815"
down_revision: str | None = "f6b3d8e1a742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINS = ("automatico", "manual")


def upgrade() -> None:
    op.add_column("ai_reports", sa.Column("encounter_id", sa.Uuid(), nullable=True))
    op.add_column(
        "ai_reports",
        sa.Column("origin", sa.String(length=32), server_default="manual", nullable=False),
    )
    # Same procedure as the audit actions: the name goes without the ck_<table>_ prefix.
    op.create_check_constraint("ai_report_origin", "ai_reports", sa.column("origin").in_(ORIGINS))
    op.create_foreign_key(
        op.f("fk_ai_reports_encounter_id_encounters"),
        "ai_reports",
        "encounters",
        ["encounter_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_ai_reports_encounter_id"), "ai_reports", ["encounter_id"], unique=False)
    # An automatic report has no human requester.
    op.alter_column("ai_reports", "requested_by", existing_type=sa.Uuid(), nullable=True)
    # Exactly one automatic report per encounter and kind, even under concurrency.
    op.create_index(
        "uq_ai_reports_auto_per_encounter",
        "ai_reports",
        ["encounter_id", "kind"],
        unique=True,
        postgresql_where=sa.text("origin = 'automatico'"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_reports_auto_per_encounter", table_name="ai_reports")
    # Rows with no requester cannot go back to NOT NULL: stop instead of inventing one.
    op.alter_column("ai_reports", "requested_by", existing_type=sa.Uuid(), nullable=False)
    op.drop_index(op.f("ix_ai_reports_encounter_id"), table_name="ai_reports")
    op.drop_constraint(op.f("fk_ai_reports_encounter_id_encounters"), "ai_reports", type_="foreignkey")
    op.drop_constraint("ai_report_origin", "ai_reports", type_="check")
    op.drop_column("ai_reports", "origin")
    op.drop_column("ai_reports", "encounter_id")
