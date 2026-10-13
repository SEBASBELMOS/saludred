"""reactive system: referrals, critical alerts and their audit actions

Revision ID: f6b3d8e1a742
Revises: e5a9c2d4b681
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6b3d8e1a742"
down_revision: str | None = "e5a9c2d4b681"
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
# REFERRAL_CREATED / REFERRAL_ACCEPTED / ALERT_* already exist; rejecting and
# completing a referral are new.
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + ("REFERRAL_REJECTED", "REFERRAL_COMPLETED")


def upgrade() -> None:
    op.create_table(
        "referrals",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("referring_user_id", sa.Uuid(), nullable=False),
        sa.Column("specialist_user_id", sa.Uuid(), nullable=False),
        sa.Column("motivo", sa.String(length=1000), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "pendiente", "aceptada", "completada", "rechazada",
                name="referral_status", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("motivo_rechazo", sa.String(length=500), nullable=True),
        sa.Column("informe_especialista", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fhir_resource_id", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_referrals_patient_id_patients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["referring_user_id"], ["users.id"],
            name=op.f("fk_referrals_referring_user_id_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["specialist_user_id"], ["users.id"],
            name=op.f("fk_referrals_specialist_user_id_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referrals")),
    )
    op.create_index(op.f("ix_referrals_patient_id"), "referrals", ["patient_id"], unique=False)
    op.create_index(
        op.f("ix_referrals_referring_user_id"), "referrals", ["referring_user_id"], unique=False
    )
    op.create_index(op.f("ix_referrals_created_at"), "referrals", ["created_at"], unique=False)
    op.create_index(
        "ix_referrals_specialist_user_id_estado",
        "referrals",
        ["specialist_user_id", "estado"],
        unique=False,
    )

    op.create_table(
        "alerts",
        sa.Column(
            "kind",
            sa.Enum(
                "paciente_critico", "cama_sin_limpiar",
                name="alert_kind", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("nivel", sa.String(length=20), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "activa", "reconocida",
                name="alert_status", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("mensaje", sa.String(length=500), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("ai_report_id", sa.Uuid(), nullable=True),
        sa.Column("bed_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_to", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nota_reconocimiento", sa.String(length=1000), nullable=True),
        sa.Column("datos", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_alerts_patient_id_patients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ai_report_id"], ["ai_reports.id"],
            name=op.f("fk_alerts_ai_report_id_ai_reports"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["bed_id"], ["locations.id"],
            name=op.f("fk_alerts_bed_id_locations"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_alerts_organization_id_organizations"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["users.id"],
            name=op.f("fk_alerts_recipient_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"],
            name=op.f("fk_alerts_acknowledged_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_index(op.f("ix_alerts_patient_id"), "alerts", ["patient_id"], unique=False)
    op.create_index(op.f("ix_alerts_organization_id"), "alerts", ["organization_id"], unique=False)
    op.create_index(
        op.f("ix_alerts_recipient_user_id"), "alerts", ["recipient_user_id"], unique=False
    )
    op.create_index("ix_alerts_estado_created_at", "alerts", ["estado", "created_at"], unique=False)
    # At most one ACTIVE critical alert per patient and one ACTIVE alert per bed.
    op.create_index(
        "uq_alerts_active_critical_patient",
        "alerts",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'paciente_critico' AND estado = 'activa'"),
    )
    op.create_index(
        "uq_alerts_active_bed",
        "alerts",
        ["bed_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'cama_sin_limpiar' AND estado = 'activa'"),
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

    op.drop_index("uq_alerts_active_bed", table_name="alerts")
    op.drop_index("uq_alerts_active_critical_patient", table_name="alerts")
    op.drop_index("ix_alerts_estado_created_at", table_name="alerts")
    op.drop_index(op.f("ix_alerts_recipient_user_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_organization_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_patient_id"), table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_referrals_specialist_user_id_estado", table_name="referrals")
    op.drop_index(op.f("ix_referrals_created_at"), table_name="referrals")
    op.drop_index(op.f("ix_referrals_referring_user_id"), table_name="referrals")
    op.drop_index(op.f("ix_referrals_patient_id"), table_name="referrals")
    op.drop_table("referrals")
