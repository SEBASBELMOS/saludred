"""agent hardening: idempotent referrals and the audit actions of the final delivery

* ``referrals.idempotency_key`` (nullable) with a partial unique index: an agent
  referral is keyed by ``<thread>:<tool_call_id>``, so retrying an approval that
  timed out can never create a second referral. Additive: no existing row changes.
* The ``audit_action`` CHECK is replaced ONCE with every action the next phases
  need (agent security events, IP block, interoperability, patient
  reconciliation, chained-audit verification).

Revision ID: a2f7c4d19b60
Revises: d3a8f2c91b47
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2f7c4d19b60"
down_revision: str | None = "d3a8f2c91b47"
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
    "AGENT_CHAT",
    "AGENT_TOOL",
    "AGENT_DECISION",
)
NEW_AUDIT_ACTIONS = (
    "AGENT_SECURITY_EVENT",
    "IP_BLOCKED",
    "INTEROP_RECEIVED",
    "INTEROP_REJECTED",
    "INTEROP_SENT",
    "PATIENT_RECONCILED",
    "AUDIT_CHAIN_VERIFIED",
)
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + NEW_AUDIT_ACTIONS

INDEX = "uq_referrals_idempotency_key"


def upgrade() -> None:
    op.add_column("referrals", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    op.create_index(
        INDEX,
        "referrals",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Same procedure as d3a8f2c91b47: the name goes without the ck_<table>_ prefix.
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
    op.drop_index(INDEX, table_name="referrals")
    op.drop_column("referrals", "idempotency_key")
