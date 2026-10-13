"""specialist and accountant roles and new audit actions

Revision ID: 441a3d8d7e6b
Revises: c7d2a91e5b48
Create Date: 2026-09-29
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "441a3d8d7e6b"
down_revision: str | None = "c7d2a91e5b48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_ROLE_CODES = ("ADMIN", "EPS_COORDINATOR", "IPS_CLINICAL_OPERATOR", "PATIENT")
NEW_ROLES = (
    (
        "SPECIALIST",
        "Especialista",
        "Recibe remisiones y atiende a los pacientes remitidos, dentro de su IPS.",
    ),
    (
        "ACCOUNTANT",
        "Contable",
        "Gestiona la facturacion; no accede a datos clinicos.",
    ),
)
ROLE_CODES = LEGACY_ROLE_CODES + tuple(code for code, _n, _d in NEW_ROLES)

LEGACY_AUDIT_ACTIONS = (
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
AUDIT_ACTIONS = LEGACY_AUDIT_ACTIONS + (
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


def upgrade() -> None:
    # Los nombres van SIN el prefijo ck_<tabla>_: la convencion de nombres lo
    # antepone (ver la nota de la migracion b1c4e7f20a13 sobre drop_constraint).
    op.drop_constraint("role_code", "roles", type_="check")
    op.create_check_constraint(
        "role_code", "roles", sa.column("code").in_(ROLE_CODES)
    )

    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(AUDIT_ACTIONS)
    )

    # Los roles los crea el seed, pero una base ya en produccion no se vuelve a
    # sembrar: se insertan aqui, de forma idempotente. El seed, a su vez, solo
    # crea los roles que falten, asi que ambos caminos conviven.
    # Valores constantes definidos arriba (sin entrada externa): se incrustan
    # como literales para que la migracion tambien funcione en modo --sql.
    for code, name, description in NEW_ROLES:
        op.execute(
            "INSERT INTO roles (id, code, name, description) "
            f"SELECT '{uuid.uuid4()}', '{code}', '{name}', '{description}' "
            f"WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = '{code}')"
        )


def downgrade() -> None:
    # Solo se borran los roles que ningun usuario referencia: un downgrade que
    # dejara cuentas huerfanas (o fallara a medias) seria peor que detenerse.
    for code, _name, _description in NEW_ROLES:
        op.execute(
            f"DELETE FROM roles WHERE code = '{code}' "
            "AND NOT EXISTS (SELECT 1 FROM users WHERE users.role_id = roles.id)"
        )

    # Si quedan filas con acciones nuevas este CHECK falla a proposito: perder
    # ese historial en un downgrade seria peor que detenerlo.
    op.drop_constraint("audit_action", "audit_log", type_="check")
    op.create_check_constraint(
        "audit_action", "audit_log", sa.column("action").in_(LEGACY_AUDIT_ACTIONS)
    )

    # Si un usuario aun usa un rol nuevo, este CHECK tambien falla a proposito.
    op.drop_constraint("role_code", "roles", type_="check")
    op.create_check_constraint(
        "role_code", "roles", sa.column("code").in_(LEGACY_ROLE_CODES)
    )
