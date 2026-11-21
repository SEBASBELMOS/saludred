"""patient field encryption: blind index, wider identifier columns, safe inventory view

ADDITIVE and data-preserving: no existing row is rewritten.

* ``patients.documento_idx`` (nullable, indexed): HMAC-SHA256 blind index of the
  normalized document. NULL until the backfill (``scripts/cifrar_pacientes.py``).
* A partial UNIQUE index on ``(document_type, documento_idx)`` WHERE the index is not
  NULL. The old unique constraint on ``(document_type, document_number)`` stays: it is
  dropped only after the backfill (see docs/ENTREGA_FINAL.md, decision D1).
* ``document_number``, ``phone``, ``email`` and ``address`` become TEXT: a value
  ``v1.<kid>.<nonce>.<ciphertext>`` is longer than the plain text it hides.
  varchar -> text needs no table rewrite in PostgreSQL.
* ``v_inventario_camas`` is recreated WITHOUT ``paciente_documento``: with the document
  encrypted the view would show the ciphertext, and a view is not the place for the
  plain document. PostgreSQL does not let a column be retyped while a view depends on
  it, so the view is dropped first and created again at the end. Nothing in the
  application reads the view.

Downgrade: widened columns go back to their old lengths. If encrypted values (longer
than the old limit) exist, the ALTER fails on purpose: decrypt them first
(``scripts/cifrar_pacientes.py`` has no reverse mode; restore from the backup taken
before the backfill).

Revision ID: e8b3a5c7d921
Revises: a2f7c4d19b60
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b3a5c7d921"
down_revision: str | None = "a2f7c4d19b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW = "v_inventario_camas"

_VIEW_BODY = """
    l.id AS cama_id,
    l.code AS cama_codigo,
    l.name AS cama_nombre,
    l.service AS servicio,
    l.status AS estado,
    o.id AS ips_id,
    o.code AS ips_codigo,
    o.name AS ips_nombre,
    p.id AS paciente_id,
    {document}
    p.first_name || ' ' || p.last_name AS paciente_nombre,
    a.assigned_at AS ocupada_desde,
    (
        SELECT MAX(e.event_at)
        FROM bed_status_events e
        WHERE e.location_id = l.id
    ) AS ultimo_cambio_estado
FROM locations l
JOIN organizations o ON o.id = l.organization_id
LEFT JOIN bed_assignments a
    ON a.location_id = l.id
    AND a.status = 'ACTIVE'
    AND a.deleted_at IS NULL
LEFT JOIN bed_requests r ON r.id = a.bed_request_id
LEFT JOIN encounters en ON en.id = r.encounter_id
LEFT JOIN patients p ON p.id = en.patient_id
WHERE l.location_type = 'BED'
    AND l.deleted_at IS NULL
"""

# The view as c7d2a91e5b48 created it (with the raw document): used by the downgrade.
VIEW_SQL_PREVIOUS = f"CREATE VIEW {VIEW} AS SELECT" + _VIEW_BODY.format(
    document="p.document_number AS paciente_documento,"
)
# The view without any document column.
VIEW_SQL = f"CREATE VIEW {VIEW} AS SELECT" + _VIEW_BODY.format(document="")

INDEX = "ix_patients_documento_idx"
UNIQUE_INDEX = "uq_patients_document_type_documento_idx"

# (column, previous type, nullable)
WIDENED = (
    ("document_number", sa.String(length=32), False),
    ("phone", sa.String(length=40), True),
    ("email", sa.String(length=200), True),
    ("address", sa.String(length=300), True),
)


def upgrade() -> None:
    op.execute(f"DROP VIEW {VIEW}")
    for column, previous, nullable in WIDENED:
        op.alter_column(
            "patients",
            column,
            type_=sa.Text(),
            existing_type=previous,
            existing_nullable=nullable,
        )
    op.execute(VIEW_SQL)

    op.add_column("patients", sa.Column("documento_idx", sa.String(length=64), nullable=True))
    op.create_index(INDEX, "patients", ["documento_idx"])
    op.create_index(
        UNIQUE_INDEX,
        "patients",
        ["document_type", "documento_idx"],
        unique=True,
        postgresql_where=sa.text("documento_idx IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(UNIQUE_INDEX, table_name="patients")
    op.drop_index(INDEX, table_name="patients")
    op.drop_column("patients", "documento_idx")

    op.execute(f"DROP VIEW {VIEW}")
    for column, previous, nullable in WIDENED:
        op.alter_column(
            "patients",
            column,
            type_=previous,
            existing_type=sa.Text(),
            existing_nullable=nullable,
        )
    op.execute(VIEW_SQL_PREVIOUS)
