"""Relational model validation: one query across the whole clinical flow.

Prints, for every bed request in the database, the full chain

    patient -> encounter -> bed request -> assigned bed -> IPS

in a single SQL statement with five JOINs. This is the acceptance query for
the relational model: if the foreign keys are right, the chain reconstructs
itself; if any relation were broken, rows would silently drop out.

Usage:
    python -m scripts.demo_join
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.core.database import SessionLocal
from app.models.beds import BedAssignment, BedRequest
from app.models.clinical import Encounter
from app.models.organization import Location, Organization
from app.models.patient import Patient


def main() -> None:
    ips = aliased(Organization)

    stmt = (
        select(
            Patient.first_name,
            Patient.last_name,
            Patient.document_number,
            Encounter.encounter_class,
            Encounter.status,
            BedRequest.required_service,
            BedRequest.status.label("request_status"),
            Location.code.label("bed_code"),
            Location.status.label("bed_status"),
            ips.name.label("ips_name"),
        )
        .join(Encounter, Encounter.patient_id == Patient.id)
        .join(BedRequest, BedRequest.encounter_id == Encounter.id)
        .outerjoin(BedAssignment, BedAssignment.bed_request_id == BedRequest.id)
        .outerjoin(Location, Location.id == BedAssignment.location_id)
        .join(ips, ips.id == Encounter.organization_id)
        .where(Patient.deleted_at.is_(None))
        .order_by(ips.name, Patient.last_name)
    )

    with SessionLocal() as db:
        rows = db.execute(stmt).all()

    if not rows:
        print("Sin filas: ejecute primero el seed (python -m scripts.seed)")
        raise SystemExit(1)

    header = (
        f"{'Paciente':28} {'Documento':12} {'Encuentro':12} "
        f"{'Solicitud':10} {'Cama':24} {'Estado cama':12} IPS"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        bed = row.bed_code or "(pendiente)"
        bed_status = row.bed_status.value if row.bed_status else "-"
        print(
            f"{row.first_name + ' ' + row.last_name:28} "
            f"{row.document_number:12} "
            f"{row.encounter_class.value + '/' + row.status.value:12} "
            f"{row.request_status.value:10} "
            f"{bed:24} {bed_status:12} {row.ips_name}"
        )
    print(f"\n{len(rows)} filas: paciente -> encuentro -> solicitud -> cama -> IPS")


if __name__ == "__main__":
    main()
