"""patient consent by destination (R32): consents table and consent audit actions

* ``consents``: one row per patient x destination x validity window. Exactly one
  destination per row -- organization (in-network) XOR interop client (partner)
  -- enforced by a CHECK; renewal is a new row and revocation records
  ``revoked_at``/``revoked_by``, so rows are never edited or deleted (care trail,
  Ley 1581).
* The ``audit_action`` CHECK of ``audit_log`` is widened ONCE with the consent
  lifecycle actions (CONSENT_CREATED, CONSENT_REVOKED), same procedure as
  a2f7c4d19b60. Batch mode keeps the swap runnable on SQLite (ALTER of a CHECK
  is unsupported there) while Postgres executes plain ALTERs.

ADDITIVE: no existing table or column is touched. The downgrade drops only what
this revision adds.

Revision ID: e2c0a1f3b4d5
Revises: b7e5d9a3c418
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2c0a1f3b4d5"
down_revision: str | None = "b7e5d9a3c418"
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
    "AGENT_SECURITY_EVENT",
    "IP_BLOCKED",
    "INTEROP_RECEIVED",
    "INTEROP_REJECTED",
    "INTEROP_SENT",
    "PATIENT_RECONCILED",
    "AUDIT_CHAIN_VERIFIED",
)
NEW_AUDIT_ACTIONS = ("CONSENT_CREATED", "CONSENT_REVOKED")
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + NEW_AUDIT_ACTIONS

# Plain IS NULL / IS NOT NULL logic on purpose: SQLite enforces CHECKs but has no
# row-counting or set operators, and the disposable verification DB is SQLite.
EXACTLY_ONE_DESTINATION = (
    "(organization_id IS NULL AND interop_client_id IS NOT NULL)"
    " OR (organization_id IS NOT NULL AND interop_client_id IS NULL)"
)


def _swap_audit_check(actions: Sequence[str]) -> None:
    """Replace the audit_action CHECK.

    PostgreSQL keeps the exact a2f7c4d19b60 procedure (plain ``audit_action``
    name, which is what the production table carries). SQLite cannot ALTER a
    CHECK, so batch mode recreates the table there; the name then comes from
    reflection, not from the metadata naming convention.
    """

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("audit_action", "audit_log", type_="check")
        op.create_check_constraint("audit_action", "audit_log", sa.column("action").in_(actions))
        return
    with op.batch_alter_table("audit_log") as batch_op:
        batch_op.drop_constraint("audit_action", type_="check")
        batch_op.create_check_constraint("audit_action", sa.column("action").in_(actions))


def upgrade() -> None:
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("interop_client_id", sa.Uuid(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            EXACTLY_ONE_DESTINATION, name=op.f("ck_consents_exactly_one_destination")
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_consents_patient_id_patients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_consents_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["interop_client_id"], ["interop_clients.id"],
            name=op.f("fk_consents_interop_client_id_interop_clients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_consents_created_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["users.id"],
            name=op.f("fk_consents_revoked_by_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(
        op.f("ix_consents_patient_id_organization_id"),
        "consents",
        ["patient_id", "organization_id"],
        unique=False,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
        sqlite_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        op.f("ix_consents_patient_id_interop_client_id"),
        "consents",
        ["patient_id", "interop_client_id"],
        unique=False,
        postgresql_where=sa.text("interop_client_id IS NOT NULL"),
        sqlite_where=sa.text("interop_client_id IS NOT NULL"),
    )
    _swap_audit_check(AUDIT_ACTIONS)


def downgrade() -> None:
    # Fails on purpose if rows with the consent actions exist: a downgrade must
    # not silently lose that history.
    _swap_audit_check(PREVIOUS_AUDIT_ACTIONS)
    op.drop_index(op.f("ix_consents_patient_id_interop_client_id"), table_name="consents")
    op.drop_index(op.f("ix_consents_patient_id_organization_id"), table_name="consents")
    op.drop_table("consents")
