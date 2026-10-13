"""vista de inventario de camas y criticidad de la solicitud

Revision ID: c7d2a91e5b48
Revises: b1c4e7f20a13
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2a91e5b48"
down_revision: str | None = "b1c4e7f20a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Una fila por cama vigente. SQL portable (PostgreSQL y SQLite): sin DISTINCT ON,
# sin casts con "::" y sin funciones propias de un motor. La cama tiene a lo
# sumo una asignacion ACTIVE (indice unico parcial), asi que el LEFT JOIN no
# duplica filas.
INVENTORY_VIEW_NAME = "v_inventario_camas"
INVENTORY_VIEW_SQL = f"""
CREATE VIEW {INVENTORY_VIEW_NAME} AS
SELECT
    l.id AS cama_id,
    l.code AS cama_codigo,
    l.name AS cama_nombre,
    l.service AS servicio,
    l.status AS estado,
    o.id AS ips_id,
    o.code AS ips_codigo,
    o.name AS ips_nombre,
    p.id AS paciente_id,
    p.document_number AS paciente_documento,
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


def upgrade() -> None:
    op.execute(INVENTORY_VIEW_SQL)

    # Puntaje 0..1 que asignara el servicio de IA; NULL mientras no exista.
    op.add_column(
        "bed_requests",
        sa.Column("criticality_score", sa.Numeric(4, 3), nullable=True),
    )
    # Nombre sin prefijo: la convencion lo expande a ck_bed_requests_criticality
    # (ver la nota de la migracion anterior sobre drop_constraint).
    op.create_check_constraint(
        "criticality",
        "bed_requests",
        sa.column("criticality_score").is_(None)
        | (
            (sa.column("criticality_score") >= 0)
            & (sa.column("criticality_score") <= 1)
        ),
    )


def downgrade() -> None:
    op.drop_constraint("criticality", "bed_requests", type_="check")
    op.drop_column("bed_requests", "criticality_score")
    op.execute(f"DROP VIEW {INVENTORY_VIEW_NAME}")
