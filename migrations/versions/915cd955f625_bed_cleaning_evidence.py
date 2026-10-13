"""evidencia de limpieza de camas

Revision ID: 915cd955f625
Revises: 441a3d8d7e6b
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "915cd955f625"
down_revision: str | None = "441a3d8d7e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Los enum son VARCHAR con CHECK, igual que en el resto del esquema. La
    # foto va en bytea: son pocas imagenes pequenas por ciclo de cama y evita
    # depender de un almacen de objetos en esta etapa.
    op.create_table(
        "bed_cleaning_records",
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "isolation",
            sa.Enum(
                "NONE", "CONTACT", "DROPLET", "SPORES",
                name="cleaning_isolation", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "disinfectant",
            sa.Enum(
                "CHLORINE_5000PPM", "QUATERNARY_AMMONIUM", "SPORICIDAL", "OTHER",
                name="cleaning_disinfectant", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "curtains_changed", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("photo", sa.LargeBinary(), nullable=True),
        sa.Column("photo_content_type", sa.String(length=40), nullable=True),
        sa.Column("photo_size", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_REVIEW", "APPROVED", "REJECTED",
                name="cleaning_review_status", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.String(length=300), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "finished_at >= started_at", name=op.f("ck_bed_cleaning_records_period_order")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_bed_cleaning_records_created_by_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["locations.id"],
            name=op.f("fk_bed_cleaning_records_location_id_locations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"],
            name=op.f("fk_bed_cleaning_records_reviewed_by_users"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bed_cleaning_records")),
    )
    op.create_index(
        op.f("ix_bed_cleaning_records_location_id"),
        "bed_cleaning_records", ["location_id"], unique=False,
    )
    op.create_index(
        op.f("ix_bed_cleaning_records_status"),
        "bed_cleaning_records", ["status"], unique=False,
    )


def downgrade() -> None:
    # Borra la evidencia de limpieza junto con la tabla: hacer downgrade es una
    # decision consciente de perderla.
    op.drop_index(op.f("ix_bed_cleaning_records_status"), table_name="bed_cleaning_records")
    op.drop_index(op.f("ix_bed_cleaning_records_location_id"), table_name="bed_cleaning_records")
    op.drop_table("bed_cleaning_records")
