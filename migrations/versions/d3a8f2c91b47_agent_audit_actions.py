"""clinical agent: audit actions for chat turns, tool calls and clinician decisions

Revision ID: d3a8f2c91b47
Revises: c9e4a6b2d503
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8f2c91b47"
down_revision: str | None = "c9e4a6b2d503"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    "RAG_QUERY",
    "RAG_SUGGESTION_APPROVED",
    "RAG_SUGGESTION_REJECTED",
)
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + (
    "AGENT_CHAT",
    "AGENT_TOOL",
    "AGENT_DECISION",
)


def upgrade() -> None:
    # Same procedure as c9e4a6b2d503: the name goes without the ck_<table>_ prefix.
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
