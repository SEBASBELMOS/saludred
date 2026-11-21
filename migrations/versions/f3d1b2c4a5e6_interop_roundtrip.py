"""Add referral correlation, partner reports and the response outbox.

Revision ID: f3d1b2c4a5e6
Revises: e2c0a1f3b4d5
No existing data or audit actions are changed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f3d1b2c4a5e6"
down_revision = "e2c0a1f3b4d5"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("interop_clients", sa.Column("outbound_env_prefix", sa.String(80), nullable=True))
    op.create_table(
        "outbound_referrals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("partner_id", sa.Uuid(), sa.ForeignKey("interop_clients.id"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("doctor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service_request_id", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("service", sa.String(200), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("response_summary", sa.String(2000), nullable=True),
        sa.Column("response_task", JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("partner_id", "patient_id", "doctor_id"):
        op.create_index(f"ix_outbound_referrals_{column}", "outbound_referrals", [column])
    op.create_table(
        "partner_diagnostic_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("referral_id", sa.Uuid(), sa.ForeignKey("referrals.id"), nullable=False),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("resource", JSON, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("referral_id", "patient_id"):
        op.create_index(f"ix_partner_diagnostic_reports_{column}", "partner_diagnostic_reports", [column])
    op.create_table(
        "interop_task_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("referral_id", sa.Uuid(), sa.ForeignKey("referrals.id"), nullable=False, unique=True),
        sa.Column("client_id", sa.String(80), nullable=False),
        sa.Column("resource", JSON, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(2000), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("interop_task_outbox")
    op.drop_table("partner_diagnostic_reports")
    op.drop_table("outbound_referrals")
    op.drop_column("interop_clients", "outbound_env_prefix")
