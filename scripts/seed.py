"""Load a synthetic but coherent dataset for development and demonstration.

The data is synthetic on purpose: the project must be demonstrable without
touching real clinical records. It is not random noise either -- every patient
has encounters, every encounter has observations, and enough bed requests are
left in different states for the network view to show something meaningful.

The random generator is seeded with a constant, so two runs produce identical
data and a rehearsed demo does not change under your feet.

Usage:
    python -m scripts.seed          # abort if data already exists
    python -m scripts.seed --reset  # delete everything first
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    Base,
    BedAssignment,
    BedRequest,
    BedStatusEvent,
    Encounter,
    Location,
    Observation,
    Organization,
    Patient,
    Role,
    User,
)
from app.models.enums import (
    AdministrativeGender,
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    DocumentType,
    EncounterClass,
    EncounterStatus,
    LocationType,
    ObservationStatus,
    OrganizationType,
    Priority,
    RoleCode,
)
from app.models.loinc import VITAL_SIGNS
from app.core.security import hash_password

RNG = random.Random(20260906)
NOW = datetime.now(timezone.utc)

ROLE_DEFINITIONS: list[tuple[RoleCode, str, str]] = [
    (
        RoleCode.ADMIN,
        "Administrador del sistema",
        "Acceso completo. Unico rol autorizado para restaurar registros eliminados.",
    ),
    (
        RoleCode.EPS_COORDINATOR,
        "Coordinador de red EPS",
        "Lectura transversal de la red y gestion de solicitudes de cama entre IPS.",
    ),
    (
        RoleCode.IPS_CLINICAL_OPERATOR,
        "Operador clinico IPS",
        "Crea y edita registros clinicos de su propia IPS. Solo puede eliminar los que creo.",
    ),
    (
        RoleCode.PATIENT,
        "Paciente",
        "Consulta unicamente su propia informacion clinica.",
    ),
]

IPS_DEFINITIONS: list[tuple[str, str, list[str]]] = [
    ("IPS-NORTE", "Clinica Norte", ["Hospitalizacion", "UCI Adultos"]),
    ("IPS-SUR", "Hospital Sur", ["Hospitalizacion", "Urgencias"]),
    ("IPS-CENTRO", "Centro Medico Central", ["Hospitalizacion", "UCI Adultos"]),
]

FIRST_NAMES = [
    "Camila", "Andres", "Valentina", "Santiago", "Mariana", "Sebastian",
    "Isabella", "Nicolas", "Sofia", "Daniel", "Laura", "Juan",
    "Gabriela", "Felipe", "Paula", "Miguel",
]
LAST_NAMES = [
    "Ramirez", "Gomez", "Torres", "Moreno", "Castillo", "Herrera",
    "Rojas", "Vargas", "Mendoza", "Salazar", "Beltran", "Quintero",
]

REASONS = [
    "Dolor abdominal agudo",
    "Descompensacion respiratoria",
    "Control postquirurgico",
    "Sindrome febril en estudio",
    "Crisis hipertensiva",
    "Deshidratacion severa",
]


def _abort_if_populated(db: Session) -> None:
    if db.scalar(select(Organization).limit(1)) is not None:
        print(
            "La base ya contiene datos. Use --reset para reconstruirla desde cero.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _reset(db: Session) -> None:
    """Empty every table.

    Deletion walks ``sorted_tables`` in reverse so children are removed before
    their parents and no foreign key is ever violated mid-way.
    """

    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    print("Datos previos eliminados.")


def seed(reset: bool = False) -> None:
    settings = get_settings()
    password_hash = hash_password(settings.seed_default_password)

    with SessionLocal() as db:
        if reset:
            _reset(db)
        else:
            _abort_if_populated(db)

        # -- Roles ---------------------------------------------------------
        roles = {
            code: Role(code=code, name=name, description=description)
            for code, name, description in ROLE_DEFINITIONS
        }
        db.add_all(roles.values())
        db.flush()

        # -- Network: one EPS coordinating three IPS ------------------------
        eps = Organization(
            code="EPS-SALUDRED",
            name="EPS SaludRed",
            organization_type=OrganizationType.EPS,
        )
        db.add(eps)
        db.flush()

        ips_list: list[Organization] = []
        for code, name, _services in IPS_DEFINITIONS:
            ips = Organization(
                code=code,
                name=name,
                organization_type=OrganizationType.IPS,
                parent_organization_id=eps.id,
            )
            db.add(ips)
            ips_list.append(ips)
        db.flush()

        # -- Users, one per role -------------------------------------------
        admin = User(
            username="admin",
            full_name="Administrador SaludRed",
            email="admin@saludred.local",
            password_hash=password_hash,
            role_id=roles[RoleCode.ADMIN].id,
        )
        coordinator = User(
            username="coordinador.eps",
            full_name="Coordinadora de Red",
            email="coordinacion@saludred.local",
            password_hash=password_hash,
            role_id=roles[RoleCode.EPS_COORDINATOR].id,
            organization_id=eps.id,
        )
        db.add_all([admin, coordinator])
        db.flush()

        operators: dict[str, User] = {}
        for ips in ips_list:
            operator = User(
                username=f"operador.{ips.code.split('-')[1].lower()}",
                full_name=f"Operador Clinico {ips.name}",
                email=f"operador@{ips.code.lower()}.local",
                password_hash=password_hash,
                role_id=roles[RoleCode.IPS_CLINICAL_OPERATOR].id,
                organization_id=ips.id,
            )
            db.add(operator)
            operators[ips.code] = operator
        db.flush()

        # Authorship of the network itself belongs to the admin account.
        eps.created_by = admin.id
        for ips in ips_list:
            ips.created_by = admin.id

        # -- Physical hierarchy: facility > ward > room > bed ---------------
        beds_by_ips: dict[str, list[Location]] = {}
        for ips, (_code, _name, services) in zip(ips_list, IPS_DEFINITIONS):
            operator = operators[ips.code]
            facility = Location(
                organization_id=ips.id,
                location_type=LocationType.FACILITY,
                code=f"{ips.code}-SEDE",
                name=f"Sede principal {ips.name}",
                created_by=admin.id,
            )
            db.add(facility)
            db.flush()

            beds_by_ips[ips.code] = []
            for service_index, service in enumerate(services, start=1):
                ward = Location(
                    organization_id=ips.id,
                    parent_location_id=facility.id,
                    location_type=LocationType.WARD,
                    code=f"{ips.code}-S{service_index}",
                    name=service,
                    service=service,
                    created_by=admin.id,
                )
                db.add(ward)
                db.flush()

                for room_index in range(1, 4):
                    room = Location(
                        organization_id=ips.id,
                        parent_location_id=ward.id,
                        location_type=LocationType.ROOM,
                        code=f"{ips.code}-S{service_index}-H{room_index}",
                        name=f"Habitacion {service_index}0{room_index}",
                        service=service,
                        created_by=admin.id,
                    )
                    db.add(room)
                    db.flush()

                    for bed_index in range(1, 3):
                        status = RNG.choice(
                            [
                                BedStatus.AVAILABLE,
                                BedStatus.AVAILABLE,
                                BedStatus.OCCUPIED,
                                BedStatus.CLEANING,
                                BedStatus.BLOCKED,
                                BedStatus.MAINTENANCE,
                            ]
                        )
                        bed = Location(
                            organization_id=ips.id,
                            parent_location_id=room.id,
                            location_type=LocationType.BED,
                            code=f"{ips.code}-S{service_index}-H{room_index}-C{bed_index}",
                            name=f"Cama {service_index}0{room_index}-{bed_index}",
                            service=service,
                            status=status,
                            created_by=operator.id,
                        )
                        db.add(bed)
                        db.flush()
                        db.add(
                            BedStatusEvent(
                                location_id=bed.id,
                                previous_status=None,
                                new_status=status,
                                reason="Carga inicial de inventario",
                                event_at=NOW - timedelta(days=3),
                                changed_by=operator.id,
                            )
                        )
                        beds_by_ips[ips.code].append(bed)
        db.flush()

        # -- Patients -------------------------------------------------------
        patients: list[Patient] = []
        for index in range(16):
            first = FIRST_NAMES[index % len(FIRST_NAMES)]
            last = LAST_NAMES[index % len(LAST_NAMES)]
            patient = Patient(
                document_type=DocumentType.CC,
                document_number=str(1032450000 + index * 137),
                first_name=first,
                last_name=last,
                birth_date=date(1955 + (index * 3) % 55, (index % 12) + 1, (index % 27) + 1),
                gender=(
                    AdministrativeGender.FEMALE
                    if index % 2 == 0
                    else AdministrativeGender.MALE
                ),
                phone=f"30{index % 10}{4000000 + index * 911}",
                email=f"{first.lower()}.{last.lower()}@example.org",
                address=f"Calle {10 + index} # {20 + index} - {index}",
                eps_organization_id=eps.id,
                created_by=admin.id,
            )
            db.add(patient)
            patients.append(patient)
        db.flush()

        # One patient gets a portal account, to demonstrate the PATIENT role.
        db.add(
            User(
                username="paciente.demo",
                full_name=patients[0].full_name,
                email=patients[0].email,
                password_hash=password_hash,
                role_id=roles[RoleCode.PATIENT].id,
                patient_id=patients[0].id,
            )
        )
        db.flush()

        # -- Encounters, observations, requests and assignments -------------
        assigned_beds: set[str] = set()
        for index, patient in enumerate(patients):
            ips = ips_list[index % len(ips_list)]
            operator = operators[ips.code]
            started = NOW - timedelta(days=RNG.randint(1, 12), hours=RNG.randint(0, 20))
            is_closed = index % 4 == 0

            encounter = Encounter(
                patient_id=patient.id,
                organization_id=ips.id,
                encounter_class=(
                    EncounterClass.EMER if index % 3 == 0 else EncounterClass.IMP
                ),
                status=(
                    EncounterStatus.FINISHED if is_closed else EncounterStatus.IN_PROGRESS
                ),
                priority=(
                    Priority.EMERGENCY
                    if index % 5 == 0
                    else (Priority.URGENT if index % 2 else Priority.ROUTINE)
                ),
                reason_text=REASONS[index % len(REASONS)],
                started_at=started,
                ended_at=started + timedelta(days=2) if is_closed else None,
                created_by=operator.id,
            )
            db.add(encounter)
            db.flush()

            for vital in RNG.sample(VITAL_SIGNS, k=3):
                value = Decimal(str(round(RNG.uniform(vital.low, vital.high), 1)))
                db.add(
                    Observation(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        status=ObservationStatus.FINAL,
                        code=vital.code,
                        display=vital.display,
                        value_numeric=value,
                        unit=vital.unit,
                        observed_at=started + timedelta(hours=RNG.randint(1, 30)),
                        created_by=operator.id,
                    )
                )

            request = BedRequest(
                encounter_id=encounter.id,
                requesting_organization_id=ips.id,
                required_service=RNG.choice(["Hospitalizacion", "UCI Adultos"]),
                priority=encounter.priority,
                status=BedRequestStatus.PENDING,
                requested_at=started + timedelta(minutes=45),
                created_by=operator.id,
            )
            db.add(request)
            db.flush()

            # Roughly two thirds of the requests get a bed, so the demo shows
            # both a resolved flow and a pending queue.
            if index % 3 != 2:
                free_beds = [
                    bed
                    for bed in beds_by_ips[ips.code]
                    if bed.code not in assigned_beds
                    and bed.status in {BedStatus.AVAILABLE, BedStatus.OCCUPIED}
                ]
                if free_beds:
                    bed = free_beds[0]
                    assigned_beds.add(bed.code)
                    request.status = BedRequestStatus.ASSIGNED
                    request.target_organization_id = ips.id
                    request.resolved_at = request.requested_at + timedelta(hours=2)
                    db.add(
                        BedAssignment(
                            bed_request_id=request.id,
                            location_id=bed.id,
                            status=(
                                BedAssignmentStatus.RELEASED
                                if is_closed
                                else BedAssignmentStatus.ACTIVE
                            ),
                            assigned_at=request.resolved_at,
                            released_at=(
                                request.resolved_at + timedelta(days=2)
                                if is_closed
                                else None
                            ),
                            created_by=operator.id,
                        )
                    )
                    previous = bed.status
                    bed.status = (
                        BedStatus.CLEANING if is_closed else BedStatus.OCCUPIED
                    )
                    encounter.location_id = bed.id
                    db.add(
                        BedStatusEvent(
                            location_id=bed.id,
                            encounter_id=encounter.id,
                            previous_status=previous,
                            new_status=bed.status,
                            reason="Asignacion de cama a solicitud",
                            event_at=request.resolved_at,
                            changed_by=operator.id,
                        )
                    )

        db.commit()

        print("Seed completado:")
        for label, model in [
            ("organizaciones", Organization),
            ("ubicaciones", Location),
            ("usuarios", User),
            ("pacientes", Patient),
            ("encuentros", Encounter),
            ("observaciones", Observation),
            ("solicitudes de cama", BedRequest),
            ("asignaciones de cama", BedAssignment),
            ("eventos de estado de cama", BedStatusEvent),
        ]:
            count = db.scalar(select(func.count()).select_from(model))
            print(f"  {count:4} {label}")
        print(f"\nContrasena de todas las cuentas de demo: {settings.seed_default_password}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga datos sinteticos de demostracion.")
    parser.add_argument(
        "--reset", action="store_true", help="Elimina los datos existentes antes de cargar."
    )
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
