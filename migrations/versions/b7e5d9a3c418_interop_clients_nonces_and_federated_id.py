"""interoperability between teams: partner clients, nonces, federated id, external referrals

ADDITIVE except one relaxation (see below). Nothing existing is rewritten, deleted or
renumbered; the audit actions it needs (INTEROP_RECEIVED/REJECTED/SENT,
PATIENT_RECONCILED) were already added by a2f7c4d19b60.

* ``interop_clients``: one row per partner system (hashed client secret, registered
  scopes, ENCRYPTED per-client signing secret).
* ``interop_nonces``: UNIQUE (client_id, nonce), the replay check of the HMAC signature.
* ``patients.id_federado``: nullable, indexed. NOT backfilled here (the existing rows
  keep NULL; the receiver resolves them by computing, and the backfill needs approval:
  docs/ENTREGA_FINAL.md, D1).
* ``fhir_inbound_events``: nullable columns that turn it into the reconciliation queue.
* ``referrals``: ``origen_externo`` and ``referencia_externa`` (nullable), and
  ``referring_user_id`` becomes NULLABLE -- a referral that arrives from a partner has no
  local sender.

THE ONLY NON-REVERSIBLE-IN-GENERAL STEP: the downgrade puts NOT NULL back on
``referrals.referring_user_id``. It fails on purpose once external referrals exist
(rows with NULL there); it checks first and says so. Delete or reassign those rows
before downgrading. Everything else downgrades by dropping what this adds.

Revision ID: b7e5d9a3c418
Revises: f1d6c4a8b250
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7e5d9a3c418"
down_revision: str | None = "f1d6c4a8b250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interop_clients",
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        sa.Column("signing_secret", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interop_clients")),
    )
    op.create_index(
        op.f("ix_interop_clients_client_id"), "interop_clients", ["client_id"], unique=True
    )

    op.create_table(
        "interop_nonces",
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interop_nonces")),
        sa.UniqueConstraint("client_id", "nonce", name="uq_interop_nonces_client_id_nonce"),
    )
    op.create_index(op.f("ix_interop_nonces_seen_at"), "interop_nonces", ["seen_at"], unique=False)

    op.add_column("patients", sa.Column("id_federado", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_patients_id_federado"), "patients", ["id_federado"], unique=False)

    op.add_column("fhir_inbound_events", sa.Column("origen_externo", sa.String(length=80), nullable=True))
    op.add_column(
        "fhir_inbound_events", sa.Column("id_federado_recibido", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "fhir_inbound_events", sa.Column("estado_conciliacion", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "fhir_inbound_events", sa.Column("motivo_pendiente", sa.String(length=40), nullable=True)
    )
    op.add_column("fhir_inbound_events", sa.Column("conciliado_por", sa.Uuid(), nullable=True))
    op.add_column(
        "fhir_inbound_events", sa.Column("conciliado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_fhir_inbound_events_conciliado_por_users"),
        "fhir_inbound_events",
        "users",
        ["conciliado_por"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_fhir_inbound_events_estado_conciliacion"),
        "fhir_inbound_events",
        ["estado_conciliacion"],
        unique=False,
    )

    op.add_column("referrals", sa.Column("origen_externo", sa.String(length=80), nullable=True))
    op.add_column("referrals", sa.Column("referencia_externa", sa.String(length=120), nullable=True))
    op.alter_column("referrals", "referring_user_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    # Fails on purpose if a partner's referral exists: it has no local sender, so NOT NULL
    # cannot come back. Better to stop than to invent a sender.
    connection = op.get_bind()
    external = connection.execute(
        sa.text("SELECT count(*) FROM referrals WHERE referring_user_id IS NULL")
    ).scalar()
    if external:
        raise RuntimeError(
            f"{external} remision(es) externa(s) sin remitente local: reasignelas o eliminelas "
            "antes de revertir (referrals.referring_user_id vuelve a ser NOT NULL)"
        )
    op.alter_column("referrals", "referring_user_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("referrals", "referencia_externa")
    op.drop_column("referrals", "origen_externo")

    op.drop_index(op.f("ix_fhir_inbound_events_estado_conciliacion"), table_name="fhir_inbound_events")
    op.drop_constraint(
        op.f("fk_fhir_inbound_events_conciliado_por_users"), "fhir_inbound_events", type_="foreignkey"
    )
    for column in (
        "conciliado_en", "conciliado_por", "motivo_pendiente", "estado_conciliacion",
        "id_federado_recibido", "origen_externo",
    ):
        op.drop_column("fhir_inbound_events", column)

    op.drop_index(op.f("ix_patients_id_federado"), table_name="patients")
    op.drop_column("patients", "id_federado")

    op.drop_index(op.f("ix_interop_nonces_seen_at"), table_name="interop_nonces")
    op.drop_table("interop_nonces")
    op.drop_index(op.f("ix_interop_clients_client_id"), table_name="interop_clients")
    op.drop_table("interop_clients")
