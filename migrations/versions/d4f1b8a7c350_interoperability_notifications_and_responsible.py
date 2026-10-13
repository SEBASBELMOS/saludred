"""interoperability: notifications, FHIR inbound events, responsible clinician

Revision ID: d4f1b8a7c350
Revises: a6e2f4c1b937
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f1b8a7c350"
down_revision: str | None = "a6e2f4c1b937"
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
    "ALERT_CREATED",
    "ALERT_ACKNOWLEDGED",
    "ALERT_ESCALATED",
    "FHIR_EVENT_RECEIVED",
    "CLEANING_EVIDENCE",
    "RECORD_VIEWED",
    "CLINICAL_RECORD_EXPORTED",
)
# FHIR_EVENT_RECEIVED already exists since 441a3d8d7e6b; the two new ones are
# the Bundle import and the change of responsible clinician.
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + ("FHIR_IMPORT", "RESPONSIBLE_CHANGED")
OBSERVATION_ORIGINS = ("local", "fhir", "import")


def upgrade() -> None:
    # Responsible clinician (R21 recipient). Nullable: when unset, events go to
    # the clinicians of the IPS of the latest encounter, then to the admins.
    op.add_column(
        "patients", sa.Column("responsible_clinician_id", sa.Uuid(), nullable=True)
    )
    op.create_index(
        op.f("ix_patients_responsible_clinician_id"),
        "patients",
        ["responsible_clinician_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_patients_responsible_clinician_id_users"),
        "patients",
        "users",
        ["responsible_clinician_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Provenance of observations. Existing rows are all local.
    op.add_column(
        "observations",
        sa.Column("origin", sa.String(length=32), nullable=False, server_default="local"),
    )
    op.create_check_constraint(
        "observation_origin", "observations", sa.column("origin").in_(OBSERVATION_ORIGINS)
    )
    op.add_column(
        "observations", sa.Column("fhir_resource_id", sa.String(length=120), nullable=True)
    )
    op.create_index(
        op.f("ix_observations_fhir_resource_id"),
        "observations",
        ["fhir_resource_id"],
        unique=False,
    )

    # Notification inbox (R18).
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_notifications_user_id_users"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_notifications_patient_id_patients"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(op.f("ix_notifications_kind"), "notifications", ["kind"], unique=False)
    op.create_index(
        op.f("ix_notifications_patient_id"), "notifications", ["patient_id"], unique=False
    )
    op.create_index(
        "ix_notifications_user_id_created_at",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )

    # Trail and idempotency of what the FHIR server delivers (R21).
    op.create_table(
        "fhir_inbound_events",
        sa.Column("fhir_resource_type", sa.String(length=80), nullable=False),
        sa.Column("fhir_resource_id", sa.String(length=120), nullable=False),
        sa.Column("fhir_version_id", sa.String(length=80), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "processed", "unknown_patient",
                name="fhir_inbound_outcome", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("patient_id", sa.Uuid(), nullable=True),
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.Column("resource", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_fhir_inbound_events_patient_id_patients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"], ["observations.id"],
            name=op.f("fk_fhir_inbound_events_observation_id_observations"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fhir_inbound_events")),
        sa.UniqueConstraint(
            "fhir_resource_type", "fhir_resource_id", "fhir_version_id",
            name="uq_fhir_inbound_events_resource_version",
        ),
    )
    op.create_index(
        op.f("ix_fhir_inbound_events_patient_id"),
        "fhir_inbound_events",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fhir_inbound_events_received_at"),
        "fhir_inbound_events",
        ["received_at"],
        unique=False,
    )

    # Same procedure as a6e2f4c1b937: the name goes without the ck_<table>_ prefix.
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

    op.drop_index(op.f("ix_fhir_inbound_events_received_at"), table_name="fhir_inbound_events")
    op.drop_index(op.f("ix_fhir_inbound_events_patient_id"), table_name="fhir_inbound_events")
    op.drop_table("fhir_inbound_events")

    op.drop_index("ix_notifications_user_id_created_at", table_name="notifications")
    op.drop_index(op.f("ix_notifications_patient_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_kind"), table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_observations_fhir_resource_id"), table_name="observations")
    op.drop_column("observations", "fhir_resource_id")
    op.drop_constraint("observation_origin", "observations", type_="check")
    op.drop_column("observations", "origin")

    op.drop_constraint(
        op.f("fk_patients_responsible_clinician_id_users"), "patients", type_="foreignkey"
    )
    op.drop_index(op.f("ix_patients_responsible_clinician_id"), table_name="patients")
    op.drop_column("patients", "responsible_clinician_id")
