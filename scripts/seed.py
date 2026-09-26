"""Load a synthetic but coherent dataset for development and demonstration.

The data is synthetic on purpose: the project must be demonstrable without
touching real clinical records. It is not random noise either -- every patient
has encounters, every encounter has observations, and enough bed requests are
left in different states for the network view to show something meaningful.

The random generator is seeded with a constant, so two runs produce identical
data and a rehearsed demo does not change under your feet.

The bed layer is not filled in at random: requests are replayed in
chronological order against a pool of beds that are occupied, released,
cleaned and reused. A bed that is busy cannot take a second patient, so
the queue that forms is a consequence of the simulation rather than a
decoration, and the wait-time statistics measure something real.

Usage:
    python -m scripts.seed                  # abort if data already exists
    python -m scripts.seed --reset          # delete everything first
    python -m scripts.seed --reset --patients 400   # dataset for analysis
"""

from __future__ import annotations

import argparse
import heapq
import math
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
    ImagingStudy,
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
    ClinicalProfile,
    DocumentType,
    EncounterClass,
    EncounterStatus,
    ImagingStudyStatus,
    LocationType,
    ObservationStatus,
    OrganizationType,
    Priority,
    RoleCode,
)
from app.core.security import hash_password
from scripts.generators import (
    cleaning_minutes,
    imaging_studies,
    pick_age,
    pick_priority,
    pick_profile,
    stay_hours,
    vital_signs,
    wait_minutes,
)

SEED_VALUE = 20260906
# Queue order used by the simulation: lower goes first. Same rule as the API.
QUEUE_RANK = {Priority.EMERGENCY: 0, Priority.URGENT: 1, Priority.ROUTINE: 2}
# Global stream, used only for the physical plant: rooms and beds, which do
# not depend on any particular patient.
RNG = random.Random(SEED_VALUE)
NOW = datetime.now(timezone.utc)


def _stream(*parts: object) -> random.Random:
    """A random stream of its own, identified by name rather than by position.

    Every patient and every visit draws from its own stream. With a single
    shared stream, adding or skipping one patient shifts every number that
    comes after it, so raising ``--patients`` from 120 to 400 would silently
    rewrite the first 120 patients and no two runs of a growing dataset would
    ever agree.
    """

    return random.Random("|".join(str(part) for part in parts))

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

CIE10_DIAGNOSES = [
    ("J00", "Rinofaringitis aguda (resfriado comun)"),
    ("I10", "Hipertension esencial"),
    ("E11", "Diabetes mellitus tipo 2"),
    ("M54", "Dorsalgia"),
    ("R51", "Cefalea"),
    ("Z00", "Examen general (control de rutina)"),
]

# Diagnoses that fit each profile. A hypertensive patient admitted for a
# common cold is possible but it should be the exception, not one case in six:
# if the diagnosis and the vital signs disagree, no analysis of the data can
# be trusted afterwards.
PROFILE_DIAGNOSES: dict[ClinicalProfile, list[tuple[str, str]]] = {
    ClinicalProfile.HEALTHY: [
        ("Z00", "Examen general (control de rutina)"),
        ("J00", "Rinofaringitis aguda (resfriado comun)"),
        ("M54", "Dorsalgia"),
    ],
    ClinicalProfile.HYPERTENSIVE: [
        ("I10", "Hipertension esencial"),
        ("I11", "Cardiopatia hipertensiva"),
        ("R51", "Cefalea"),
    ],
    ClinicalProfile.DIABETIC: [
        ("E11", "Diabetes mellitus tipo 2"),
        ("E11.6", "Diabetes mellitus tipo 2 con complicaciones"),
        ("N18", "Enfermedad renal cronica"),
    ],
    ClinicalProfile.CARDIAC: [
        ("I50", "Insuficiencia cardiaca"),
        ("I21", "Infarto agudo de miocardio"),
        ("I48", "Fibrilacion auricular"),
    ],
    ClinicalProfile.RESPIRATORY: [
        ("J44", "Enfermedad pulmonar obstructiva cronica"),
        ("J18", "Neumonia, microorganismo no especificado"),
        ("J45", "Asma"),
    ],
    ClinicalProfile.ELDERLY_FRAIL: [
        ("S72", "Fractura de femur"),
        ("R26", "Anomalias de la marcha y de la movilidad"),
        ("F03", "Demencia no especificada"),
    ],
}


def _diagnosis_reason(rng: random.Random, profile: ClinicalProfile) -> str:
    """Compose an encounter reason from a CIE-10 code that fits the profile.

    The vocabulary is the same the professor used in the week-3 synthetic-data
    example; what is added here is the link between the diagnosis and the rest
    of the record.
    """

    code, description = rng.choice(PROFILE_DIAGNOSES[profile])
    return f"{code} - {description}"


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


# Assumptions used to size the network against its own demand. They are
# averages of what the generator produces; the simulation is what decides the
# actual numbers.
MEAN_VISITS_PER_PATIENT = 1.55
MEAN_STAY_DAYS = 2.6
OUT_OF_SERVICE_SHARE = 0.07
# A hospital network running at 40% occupancy has no coordination problem to
# solve: every request finds a bed immediately and the priority queue becomes
# decoration. Sizing the plant against the demand is what makes scarcity --
# and therefore the whole project -- visible in the data.
TARGET_OCCUPANCY = 0.85


def _plant_size(patient_count: int) -> tuple[int, int]:
    """Return ``(window_days, rooms_per_ward)`` sized against the demand.

    Both numbers come from the same arithmetic a planner would do: how many
    bed-days the expected admissions consume, and how many beds are needed to
    absorb them at the target occupancy.
    """

    window_days = max(7, min(90, patient_count // 8))
    bed_days = patient_count * MEAN_VISITS_PER_PATIENT * MEAN_STAY_DAYS
    beds = bed_days / (window_days * TARGET_OCCUPANCY * (1 - OUT_OF_SERVICE_SHARE))
    # The hierarchy is fixed at 3 IPS x 2 wards x N rooms x 2 beds, so the
    # only free variable is the number of rooms. Two is the floor: with a
    # single room the location tree stops being a tree.
    rooms_per_ward = max(2, math.ceil(beds / 12))
    return window_days, rooms_per_ward


def seed(reset: bool = False, patient_count: int = 16) -> None:
    # Re-seed on every call, not only on import. Otherwise a second call in
    # the same process picks up the stream where the first one left it and
    # produces different data -- which would quietly break the reproducibility
    # this module promises.
    RNG.seed(SEED_VALUE)

    window_days, rooms_per_ward = _plant_size(patient_count)

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

                for room_index in range(1, rooms_per_ward + 1):
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
                        # A small share of the plant is out of service for
                        # reasons unrelated to patients. Everything else
                        # starts free and the simulation below decides where
                        # it ends up, so the final status always has a
                        # traceable history behind it.
                        status = (
                            RNG.choice([BedStatus.BLOCKED, BedStatus.MAINTENANCE])
                            if RNG.random() < 0.07
                            else BedStatus.AVAILABLE
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
                                event_at=NOW - timedelta(days=window_days + 1),
                                changed_by=operator.id,
                            )
                        )
                        beds_by_ips[ips.code].append(bed)
        db.flush()

        # -- Patients -------------------------------------------------------
        # Each patient carries a clinical profile. It is not decoration: the
        # profile drives the vital signs, the triage priority, the length of
        # stay and the imaging that gets ordered, and it is kept in the row
        # as the ground truth for checking whether an automatic grouping
        # recovers the structure that generated the data.
        patients: list[Patient] = []
        for index in range(patient_count):
            prng = _stream(SEED_VALUE, "paciente", index)
            profile = pick_profile(prng)
            age = pick_age(prng, profile)
            gender = (
                AdministrativeGender.FEMALE
                if index % 2 == 0
                else AdministrativeGender.MALE
            )
            first = FIRST_NAMES[index % len(FIRST_NAMES)]
            last = LAST_NAMES[(index // len(FIRST_NAMES) + index) % len(LAST_NAMES)]
            birth_year = NOW.year - age
            patient = Patient(
                document_type=DocumentType.CC,
                document_number=str(1032450000 + index * 137),
                first_name=first,
                last_name=last,
                birth_date=date(birth_year, (index % 12) + 1, (index % 27) + 1),
                gender=gender,
                phone=f"30{index % 10}{4000000 + index * 911}",
                email=f"{first.lower()}.{last.lower()}.{index}@example.org",
                address=f"Calle {10 + index % 90} # {20 + index % 60} - {index % 100}",
                eps_organization_id=eps.id,
                clinical_profile=profile,
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

        # -- Encounters ------------------------------------------------------
        # Encounters are built first and then replayed in chronological order.
        # Building them in patient order and assigning beds on the way would
        # let a patient admitted today take a bed that another patient is
        # still occupying next week.
        planned: list[dict] = []
        for index, patient in enumerate(patients):
            profile = patient.clinical_profile
            # Sicker profiles come back more often. A network where everyone
            # is admitted exactly once cannot show a readmission.
            vcount = _stream(SEED_VALUE, "paciente", index, "visitas")
            visits = vcount.choices(
                [1, 2, 3],
                weights=(
                    [0.80, 0.17, 0.03]
                    if profile == ClinicalProfile.HEALTHY
                    else [0.58, 0.30, 0.12]
                ),
                k=1,
            )[0]
            for visit in range(visits):
                # Each visit carries its own stream, so the record of one
                # encounter never depends on how many came before it.
                vrng = _stream(SEED_VALUE, "paciente", index, "visita", visit)
                # Day 0 is included on purpose: without arrivals in the last
                # hours, everything would already have been served and the
                # queue would only hold old leftovers instead of the people
                # who are actually waiting right now.
                started = NOW - timedelta(
                    days=vrng.randint(0, window_days),
                    hours=vrng.randint(0, 23),
                    minutes=vrng.randint(0, 59),
                )
                # Leave room for the request itself (20-90 min after the
                # arrival) so nothing is timestamped in the future.
                started = min(started, NOW - timedelta(minutes=vrng.randint(100, 600)))
                planned.append(
                    {
                        "patient": patient,
                        "profile": profile,
                        "ips": ips_list[(index + visit) % len(ips_list)],
                        "started": started,
                        "priority": pick_priority(vrng, profile),
                        "rng": vrng,
                        # Stable base for the DICOM identifiers. Deriving
                        # them from a running counter would renumber every
                        # existing study as soon as one patient is added.
                        "uid_base": index * 1000 + visit * 10,
                    }
                )

        planned.sort(key=lambda item: item["started"])

        # -- Bed pool --------------------------------------------------------
        # ``free_from`` is the instant each bed becomes assignable again:
        # after the patient leaves AND after the cleaning is finished. That
        # second part is the whole point of tracking CLEANING as a state --
        # a bed that is empty but dirty does not help anyone.
        free_from: dict[int, datetime] = {}
        status_now: dict[int, BedStatus] = {}
        demand: dict[str, list[dict]] = {ips.code: [] for ips in ips_list}
        pool: dict[str, list[Location]] = {}
        for ips in ips_list:
            usable = [
                bed
                for bed in beds_by_ips[ips.code]
                if bed.status == BedStatus.AVAILABLE
            ]
            pool[ips.code] = usable
            for bed in usable:
                free_from[bed.id] = NOW - timedelta(days=window_days + 1)

        for item in planned:
            patient = item["patient"]
            profile = item["profile"]
            ips = item["ips"]
            started = item["started"]
            priority = item["priority"]
            operator = operators[ips.code]
            vrng: random.Random = item["rng"]
            uid_base: int = item["uid_base"]

            encounter = Encounter(
                patient_id=patient.id,
                organization_id=ips.id,
                encounter_class=(
                    EncounterClass.EMER
                    if priority == Priority.EMERGENCY
                    else EncounterClass.IMP
                ),
                status=EncounterStatus.IN_PROGRESS,
                priority=priority,
                reason_text=_diagnosis_reason(vrng, profile),
                started_at=started,
                created_by=operator.id,
            )
            db.add(encounter)
            db.flush()

            for reading in vital_signs(vrng, profile, patient.gender):
                db.add(
                    Observation(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        status=ObservationStatus.FINAL,
                        code=reading.code,
                        display=reading.display,
                        value_numeric=Decimal(str(reading.value)),
                        unit=reading.unit,
                        observed_at=min(NOW, started + timedelta(minutes=vrng.randint(10, 180))),
                        created_by=operator.id,
                    )
                )

            for study in imaging_studies(vrng, profile, started, uid_base):
                db.add(
                    ImagingStudy(
                        patient_id=patient.id,
                        encounter_id=encounter.id,
                        organization_id=ips.id,
                        study_instance_uid=study.study_instance_uid,
                        accession_number=study.accession_number,
                        modality=study.modality,
                        status=(
                            ImagingStudyStatus.AVAILABLE
                            if study.started_at <= NOW
                            else ImagingStudyStatus.REGISTERED
                        ),
                        body_site=study.body_site,
                        description=study.description,
                        series_count=study.series_count,
                        instance_count=study.instance_count,
                        started_at=study.started_at,
                        created_by=operator.id,
                    )
                )

            requested_at = started + timedelta(minutes=vrng.randint(20, 90))
            request = BedRequest(
                encounter_id=encounter.id,
                requesting_organization_id=ips.id,
                required_service=vrng.choice(["Hospitalizacion", "UCI Adultos"]),
                priority=priority,
                status=BedRequestStatus.PENDING,
                requested_at=requested_at,
                created_by=operator.id,
            )
            db.add(request)
            db.flush()

            # -- Bed assignment happens after the loop -----------------------
            # Everything random about this stay is drawn now, from this
            # visit's own stream, so the simulation below cannot change which
            # numbers each patient gets.
            demand[ips.code].append(
                {
                    "seq": len(demand[ips.code]),
                    "request": request,
                    "encounter": encounter,
                    "operator": operator,
                    "ips": ips,
                    "priority": priority,
                    "requested_at": requested_at,
                    "ready_at": requested_at
                    + timedelta(minutes=wait_minutes(vrng, priority)),
                    "stay": timedelta(hours=stay_hours(vrng, profile, priority)),
                    "cleaning": timedelta(minutes=cleaning_minutes(vrng)),
                }
            )

        # -- Bed simulation, one priority queue per IPS -----------------------
        # Whenever a bed frees up it goes to the most urgent request already
        # waiting, and among equals to the one that arrived first: the same
        # rule the API enforces. An earlier version served requests in arrival
        # order, and under saturation an emergency waited behind every routine
        # admission -- two days at the 90th percentile.
        def record(item: dict, bed: Location, assigned_at: datetime) -> None:
            request = item["request"]
            encounter = item["encounter"]
            operator = item["operator"]
            discharged_at = assigned_at + item["stay"]
            clean_until = discharged_at + item["cleaning"]

            # Status at the moment of the snapshot. Each bed receives its
            # assignments in time order, so the last one written holds it now.
            if clean_until <= NOW:
                status_now[bed.id] = BedStatus.AVAILABLE
            elif discharged_at <= NOW:
                status_now[bed.id] = BedStatus.CLEANING
            else:
                status_now[bed.id] = BedStatus.OCCUPIED

            request.status = BedRequestStatus.ASSIGNED
            request.target_organization_id = item["ips"].id
            request.resolved_at = assigned_at
            encounter.location_id = bed.id

            db.add(
                BedStatusEvent(
                    location_id=bed.id,
                    encounter_id=encounter.id,
                    previous_status=BedStatus.AVAILABLE,
                    new_status=BedStatus.OCCUPIED,
                    reason="Asignacion de cama a solicitud",
                    event_at=assigned_at,
                    changed_by=operator.id,
                )
            )
            discharged = discharged_at <= NOW
            db.add(
                BedAssignment(
                    bed_request_id=request.id,
                    location_id=bed.id,
                    status=(
                        BedAssignmentStatus.RELEASED
                        if discharged
                        else BedAssignmentStatus.ACTIVE
                    ),
                    assigned_at=assigned_at,
                    released_at=discharged_at if discharged else None,
                    created_by=operator.id,
                )
            )
            if not discharged:
                return
            encounter.status = EncounterStatus.FINISHED
            encounter.ended_at = discharged_at
            # Release never goes straight to AVAILABLE: the bed is dirty until
            # someone cleans it, and the gap between these two events is the
            # turnaround time the network is measured on.
            db.add(
                BedStatusEvent(
                    location_id=bed.id,
                    encounter_id=encounter.id,
                    previous_status=BedStatus.OCCUPIED,
                    new_status=BedStatus.CLEANING,
                    reason="Egreso del paciente",
                    event_at=discharged_at,
                    changed_by=operator.id,
                )
            )
            if clean_until <= NOW:
                db.add(
                    BedStatusEvent(
                        location_id=bed.id,
                        encounter_id=encounter.id,
                        previous_status=BedStatus.CLEANING,
                        new_status=BedStatus.AVAILABLE,
                        reason="Aseo terminal completado",
                        event_at=clean_until,
                        changed_by=operator.id,
                    )
                )

        for ips in ips_list:
            beds_here = pool[ips.code]
            if not beds_here:
                continue
            # (time the bed is free, stable tie-breaker, bed)
            free_beds = [(free_from[b.id], n, b) for n, b in enumerate(beds_here)]
            heapq.heapify(free_beds)
            arrivals = sorted(demand[ips.code], key=lambda it: (it["ready_at"], it["seq"]))
            waiting: list[tuple] = []
            k = 0

            def admit(item: dict) -> None:
                heapq.heappush(
                    waiting,
                    (QUEUE_RANK[item["priority"]], item["requested_at"], item["seq"], item),
                )

            while True:
                if not waiting:
                    if k >= len(arrivals):
                        break
                    admit(arrivals[k])
                    k += 1
                bed_free_at, n, bed = free_beds[0]
                # The next bed can be handed out once it is free and someone
                # is ready for it; everyone ready by then competes for it.
                decision_at = max(bed_free_at, min(w[3]["ready_at"] for w in waiting))
                while k < len(arrivals) and arrivals[k]["ready_at"] <= decision_at:
                    admit(arrivals[k])
                    k += 1
                item = waiting[0][3]
                # At least one minute after the bed is ready: assigning at the
                # very instant the cleaning ends would give the "available"
                # and "occupied" events the same timestamp, and reading the
                # history in time order would then pick one at random.
                assigned_at = max(item["ready_at"], bed_free_at + timedelta(minutes=1))
                if assigned_at > NOW:
                    # From here on every turn falls after the snapshot: the
                    # requests still waiting are the queue as it stands now.
                    break
                heapq.heappop(waiting)
                heapq.heappop(free_beds)
                record(item, bed, assigned_at)
                free_again = assigned_at + item["stay"] + item["cleaning"]
                free_from[bed.id] = free_again
                heapq.heappush(free_beds, (free_again, n, bed))

        # The current status of every bed comes from the simulation itself.
        # An earlier version re-read it from the event history ordered by
        # time; when two events shared a timestamp the order was arbitrary,
        # and 27 beds holding a patient were shown as available.
        for ips in ips_list:
            for bed in pool[ips.code]:
                bed.status = status_now.get(bed.id, BedStatus.AVAILABLE)

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
            ("estudios de imagen", ImagingStudy),
        ]:
            count = db.scalar(select(func.count()).select_from(model))
            print(f"  {count:4} {label}")
        pending = db.scalar(
            select(func.count())
            .select_from(BedRequest)
            .where(BedRequest.status == BedRequestStatus.PENDING)
        )
        print(f"\n  {pending} solicitudes quedaron en espera (cola real de la simulacion)")
        print(f"\nContrasena de todas las cuentas de demo: {settings.seed_default_password}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Carga datos sinteticos de demostracion.")
    parser.add_argument(
        "--reset", action="store_true", help="Elimina los datos existentes antes de cargar."
    )
    parser.add_argument(
        "--patients",
        type=int,
        default=16,
        help=(
            "Numero de pacientes. El valor por defecto carga el conjunto minimo "
            "de demostracion; use varios cientos para obtener un volumen util "
            "para analisis estadistico y agrupamiento."
        ),
    )
    args = parser.parse_args()
    if args.patients < 1:
        parser.error("--patients debe ser mayor que cero")
    seed(reset=args.reset, patient_count=args.patients)


if __name__ == "__main__":
    main()
