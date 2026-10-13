"""ai reports with clinical sign-off and cleaning delay prediction

Revision ID: a6e2f4c1b937
Revises: 3f8a6b1d9c27
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a6e2f4c1b937"
down_revision: str | None = "3f8a6b1d9c27"
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
)
AUDIT_ACTIONS = PREVIOUS_AUDIT_ACTIONS + ("CLINICAL_RECORD_EXPORTED",)


def upgrade() -> None:
    # Reportes de IA con firma clinica (R10). Payload y respuesta del modelo se
    # escriben una sola vez; la tabla no tiene borrado: un reporte rechazado
    # queda como evidencia.
    op.create_table(
        "ai_reports",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "criticality",
                name="ai_report_kind", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("pseudonym", sa.String(length=32), nullable=False),
        sa.Column("payload_sent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("probability", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pendiente", "aprobado", "rechazado",
                name="ai_report_status", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("applied_bed_request_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"],
            name=op.f("fk_ai_reports_patient_id_patients"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"],
            name=op.f("fk_ai_reports_requested_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"],
            name=op.f("fk_ai_reports_reviewed_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["applied_bed_request_id"], ["bed_requests.id"],
            name=op.f("fk_ai_reports_applied_bed_request_id_bed_requests"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_reports")),
    )
    op.create_index(op.f("ix_ai_reports_patient_id"), "ai_reports", ["patient_id"], unique=False)
    op.create_index(op.f("ix_ai_reports_pseudonym"), "ai_reports", ["pseudonym"], unique=False)
    op.create_index(op.f("ix_ai_reports_is_critical"), "ai_reports", ["is_critical"], unique=False)
    op.create_index(op.f("ix_ai_reports_status"), "ai_reports", ["status"], unique=False)
    op.create_index(op.f("ix_ai_reports_created_at"), "ai_reports", ["created_at"], unique=False)

    # Prediccion de retraso de limpieza: la hace el servicio de IA cuando la cama
    # entra a limpieza (evento) y se copia al registro de limpieza. Nula si el
    # servicio no respondio.
    for table in ("bed_status_events", "bed_cleaning_records"):
        op.add_column(
            table,
            sa.Column("predicted_delay_probability", sa.Numeric(precision=5, scale=4), nullable=True),
        )
        op.add_column(table, sa.Column("predicted_delay", sa.Boolean(), nullable=True))
        op.add_column(table, sa.Column("delay_model_version", sa.String(length=80), nullable=True))

    # Mismo procedimiento que 441a3d8d7e6b: el nombre va sin el prefijo ck_<tabla>_.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(AUDIT_ACTIONS)
    )


def downgrade() -> None:
    # Si ya hay filas con la accion nueva, este CHECK falla a proposito: perder
    # ese historial en un downgrade seria peor que detenerlo.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(PREVIOUS_AUDIT_ACTIONS)
    )

    for table in ("bed_cleaning_records", "bed_status_events"):
        op.drop_column(table, "delay_model_version")
        op.drop_column(table, "predicted_delay")
        op.drop_column(table, "predicted_delay_probability")

    # Borra los reportes de IA: son evidencia, asi que solo se llega aqui a proposito.
    op.drop_index(op.f("ix_ai_reports_created_at"), table_name="ai_reports")
    op.drop_index(op.f("ix_ai_reports_status"), table_name="ai_reports")
    op.drop_index(op.f("ix_ai_reports_is_critical"), table_name="ai_reports")
    op.drop_index(op.f("ix_ai_reports_pseudonym"), table_name="ai_reports")
    op.drop_index(op.f("ix_ai_reports_patient_id"), table_name="ai_reports")
    op.drop_table("ai_reports")
