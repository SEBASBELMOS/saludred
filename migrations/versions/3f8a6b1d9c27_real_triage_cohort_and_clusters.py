"""real triage cohort and clusters

Revision ID: 3f8a6b1d9c27
Revises: 915cd955f625
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f8a6b1d9c27"
down_revision: str | None = "915cd955f625"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cohorte de triage: filas reales (MIMIC-IV-ED Demo) y filas sinteticas
    # generadas dentro de cada grupo. Los enum son VARCHAR con CHECK, como en
    # el resto del esquema.
    op.create_table(
        "cohort_clusters",
        sa.Column("cluster_index", sa.Integer(), nullable=False),
        sa.Column(
            "zone",
            sa.Enum(
                "FAR", "INTERMEDIATE", "NEAR",
                name="cohort_zone", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=600), nullable=False),
        sa.Column("admission_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("centroid", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("silhouette", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cohort_clusters")),
        sa.UniqueConstraint("cluster_index", name=op.f("uq_cohort_clusters_cluster_index")),
    )
    op.create_table(
        "cohort_records",
        sa.Column(
            "origin",
            sa.Enum(
                "REAL", "SYNTHETIC",
                name="cohort_origin", native_enum=False,
                create_constraint=True, length=32,
            ),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("temperature_c", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("heart_rate", sa.Integer(), nullable=False),
        sa.Column("resp_rate", sa.Integer(), nullable=False),
        sa.Column("o2sat", sa.Integer(), nullable=False),
        sa.Column("sbp", sa.Integer(), nullable=False),
        sa.Column("dbp", sa.Integer(), nullable=False),
        sa.Column("pain", sa.Integer(), nullable=True),
        sa.Column("acuity", sa.Integer(), nullable=False),
        sa.Column("admitted", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("acuity BETWEEN 1 AND 5", name=op.f("ck_cohort_records_acuity_range")),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["cohort_clusters.id"],
            name=op.f("fk_cohort_records_cluster_id_cohort_clusters"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cohort_records")),
    )
    op.create_index(
        op.f("ix_cohort_records_cluster_id"), "cohort_records", ["cluster_id"], unique=False
    )


def downgrade() -> None:
    # Borra la cohorte completa: se reconstruye con scripts.build_cohort.
    op.drop_index(op.f("ix_cohort_records_cluster_id"), table_name="cohort_records")
    op.drop_table("cohort_records")
    op.drop_table("cohort_clusters")
