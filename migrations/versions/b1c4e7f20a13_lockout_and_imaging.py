"""bloqueo de cuentas, perfil clinico y estudios de imagen

Revision ID: b1c4e7f20a13
Revises: 9245a9db9e2f
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c4e7f20a13"
down_revision: str | None = "9245a9db9e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Los enum se materializan como VARCHAR con CHECK, igual que en el resto del
# esquema: alterar un tipo ENUM nativo desde Alembic exige DDL escrito a mano.
MODALITIES = ("CR", "DX", "CT", "MR", "US", "XA", "NM")
STUDY_STATUSES = ("registered", "available", "cancelled")
PROFILES = (
    "HEALTHY",
    "HYPERTENSIVE",
    "DIABETIC",
    "CARDIAC",
    "RESPIRATORY",
    "ELDERLY_FRAIL",
)

# Acciones de auditoria que el modelo conoce HOY. La migracion inicial dejo
# audit_log.action con un CHECK de solo seis valores; el bloqueo de cuentas
# escribe tres acciones nuevas y PostgreSQL rechazaria esos registros.
AUDIT_ACTIONS = (
    "LOGIN",
    "LOGIN_FAILED",
    "ACCOUNT_LOCKED",
    "ACCOUNT_UNLOCKED",
    "CREATE",
    "SOFT_EDIT",
    "SOFT_DELETE",
    "RESTORE",
    "FHIR_SYNC",
)
LEGACY_AUDIT_ACTIONS = (
    "LOGIN",
    "CREATE",
    "SOFT_EDIT",
    "SOFT_DELETE",
    "RESTORE",
    "FHIR_SYNC",
)


def upgrade() -> None:
    # --- Bloqueo de cuentas -------------------------------------------------
    # server_default en failed_login_attempts para que las filas que ya existen
    # queden en cero sin un UPDATE aparte.
    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Ampliar la auditoria ------------------------------------------------
    # La migracion inicial dejo audit_log.action con un CHECK de solo seis
    # valores. Con la convencion de nombres activa (target_metadata), el
    # Enum "audit_action" se materializo como ck_audit_log_audit_action:
    # se reemplaza por el mismo nombre con las nueve acciones actuales. Las
    # filas existentes usan valores del subconjunto original y son validas.
    # OJO: alembic vuelve a aplicar la convencion al nombre que recibe
    # drop_constraint (toimpl.py -> schema_obj.generic_constraint), asi que
    # aca va el nombre SIN prefijo: "audit_action", que se expande a
    # ck_audit_log_audit_action en el DDL real. Pasar el nombre completo
    # produciria ck_audit_log_ck_audit_log_audit_action y fallaria.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action",
        "audit_log",
        sa.column("action").in_(AUDIT_ACTIONS),
    )

    # --- Perfil clinico del paciente ---------------------------------------
    op.add_column(
        "patients",
        sa.Column("clinical_profile", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "clinical_profile",
        "patients",
        sa.column("clinical_profile").in_(PROFILES),
    )
    op.create_index(
        "ix_patients_clinical_profile", "patients", ["clinical_profile"]
    )

    # --- Estudios de imagen -------------------------------------------------
    op.create_table(
        "imaging_studies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("encounter_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("study_instance_uid", sa.String(length=120), nullable=False),
        sa.Column("accession_number", sa.String(length=40), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("body_site", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("series_count", sa.Integer(), nullable=False),
        sa.Column("instance_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pacs_study_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_by", sa.Uuid(), nullable=True),
        # op.f() marca el nombre como definitivo. Sin el, la convencion de
        # nombres le vuelve a anteponer "ck_<tabla>_" y el DDL sale como
        # ck_imaging_studies_ck_imaging_studies_..., distinto del nombre que
        # espera el modelo. Es el mismo criterio de la migracion inicial.
        sa.CheckConstraint(
            "modality IN " + str(MODALITIES),
            name=op.f("ck_imaging_studies_imaging_modality"),
        ),
        sa.CheckConstraint(
            "status IN " + str(STUDY_STATUSES),
            name=op.f("ck_imaging_studies_imaging_study_status"),
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], name="fk_imaging_studies_patient_id_patients", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["encounter_id"], ["encounters.id"], name="fk_imaging_studies_encounter_id_encounters", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_imaging_studies_organization_id_organizations", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_imaging_studies_created_by_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_imaging_studies_updated_by_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deleted_by"], ["users.id"], name="fk_imaging_studies_deleted_by_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["restored_by"], ["users.id"], name="fk_imaging_studies_restored_by_users", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_imaging_studies"),
        sa.UniqueConstraint("study_instance_uid", name="uq_imaging_studies_study_instance_uid"),
        sa.UniqueConstraint("accession_number", name="uq_imaging_studies_accession_number"),
    )
    op.create_index("ix_imaging_studies_patient_id", "imaging_studies", ["patient_id"])
    op.create_index("ix_imaging_studies_encounter_id", "imaging_studies", ["encounter_id"])
    op.create_index("ix_imaging_studies_organization_id", "imaging_studies", ["organization_id"])
    op.create_index("ix_imaging_studies_modality", "imaging_studies", ["modality"])
    op.create_index("ix_imaging_studies_started_at", "imaging_studies", ["started_at"])
    op.create_index("ix_imaging_studies_deleted_at", "imaging_studies", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("imaging_studies")
    op.drop_index("ix_patients_clinical_profile", table_name="patients")
    op.drop_constraint("clinical_profile", "patients", type_="check")
    op.drop_column("patients", "clinical_profile")
    op.drop_column("users", "locked_at")
    op.drop_column("users", "failed_login_attempts")
    # Devolver la auditoria a los seis valores originales. Si ya se hubieran
    # registrado acciones nuevas, este CHECK fallaria a proposito: perder
    # ese historial en un downgrade seria peor que detenerlo.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action",
        "audit_log",
        sa.column("action").in_(LEGACY_AUDIT_ACTIONS),
    )
