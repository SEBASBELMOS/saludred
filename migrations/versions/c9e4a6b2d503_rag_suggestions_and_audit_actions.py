"""clinical RAG: suggestions awaiting clinician sign-off, and their audit actions

Revision ID: c9e4a6b2d503
Revises: b8d3f5a1c492
Create Date: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e4a6b2d503"
down_revision: str | None = "b8d3f5a1c492"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODES = ("llm", "extractivo", "lexico", "abstencion")
STATUSES = ("pendiente", "aprobado", "rechazado")

PREVIOUS_AUDIT_ACTIONS = (
    "LOGIN",
    "LOGIN_FAILED",
    "ACCOUNT_LOCKED",
    "ACCOUNT_UNLOCKED",
    "CREATE",
    "SOFT_EDIT",
    "SOFT_DELETE",
    "RESTORE",
    "FHIR_SYNC",
    "AI_ANALYSIS",
    "REPORT_APPROVED",
    "REPORT_REJECTED",
    "REFERRAL_CREATED",
    "REFERRAL_ACCEPTED",
    "REFERRAL_REJECTED",
    "REFERRAL_COMPLETED",
    "INVOICE_CREATED",
    "INVOICE_PAID",
    "INVOICE_VOIDED",
    "ALERT_CREATED",
    "ALERT_ACKNOWLEDGED",
    "ALERT_ESCALATED",
    "FHIR_EVENT_RECEIVED",
    "CLEANING_EVIDENCE",
    "RECORD_VIEWED",
    "CLINICAL_RECORD_EXPORTED",
    "FHIR_IMPORT",
    "RESPONSIBLE_CHANGED",
)
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + (
    "RAG_QUERY",
    "RAG_SUGGESTION_APPROVED",
    "RAG_SUGGESTION_REJECTED",
)


def upgrade() -> None:
    op.create_table(
        "rag_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_sent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_rag_suggestions_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_rag_suggestions_requested_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_rag_suggestions_reviewed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_suggestions")),
    )
    op.create_check_constraint("rag_answer_mode", "rag_suggestions", sa.column("mode").in_(MODES))
    op.create_check_constraint(
        "rag_suggestion_status", "rag_suggestions", sa.column("status").in_(STATUSES)
    )
    op.create_index(
        op.f("ix_rag_suggestions_patient_id"), "rag_suggestions", ["patient_id"], unique=False
    )
    op.create_index(op.f("ix_rag_suggestions_status"), "rag_suggestions", ["status"], unique=False)
    op.create_index(
        op.f("ix_rag_suggestions_created_at"), "rag_suggestions", ["created_at"], unique=False
    )

    # Same procedure as e5a9c2d4b681: the name goes without the ck_<table>_ prefix.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(AUDIT_ACTIONS)
    )


def downgrade() -> None:
    # If rows with the new actions exist this CHECK fails on purpose: losing
    # that history in a downgrade would be worse than stopping.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(PREVIOUS_AUDIT_ACTIONS)
    )
    op.drop_table("rag_suggestions")
