"""Coherence of the generated dataset, checked on a real schema.

The schema is built in SQLite in memory, so these tests need no server and no
credentials, but they are not unit tests: they run the whole seed and then
interrogate the result the way an auditor would. Rules like "a bed cannot hold
two patients at once" are invisible to a test that only looks at one function.
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

import scripts.seed as seed_module
from app.models import (
    Base,
    BedAssignment,
    BedRequest,
    BedStatusEvent,
    Encounter,
    ImagingStudy,
    Location,
    Observation,
    Patient,
)
from app.models.enums import (
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    EncounterStatus,
    LocationType,
)
from app.models.loinc import PLAUSIBLE_RANGE

PATIENTS = 120


@pytest.fixture(scope="module")
def db() -> Session:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:  # noqa: ANN001
        # SQLite ignores foreign keys unless asked. Without this the test
        # would pass on data Postgres would have rejected.
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        seed_module.seed(reset=True, patient_count=PATIENTS)
    finally:
        seed_module.SessionLocal = original

    with factory() as session:
        yield session


def test_the_dataset_has_volume_in_every_table(db: Session) -> None:
    for model in (Patient, Encounter, Observation, BedRequest, BedAssignment,
                  BedStatusEvent, ImagingStudy):
        count = db.scalar(select(func.count()).select_from(model))
        assert count > 0, f"{model.__tablename__} quedo vacia"

    assert db.scalar(select(func.count()).select_from(Patient)) == PATIENTS


def test_every_patient_carries_a_clinical_profile(db: Session) -> None:
    """The profile is the ground truth for evaluating any grouping later.

    A patient without one is a row that cannot be scored.
    """

    missing = db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(Patient.clinical_profile.is_(None))
    )
    assert missing == 0


def test_no_bed_ever_holds_two_patients_at_once(db: Session) -> None:
    """The failure this whole project exists to prevent.

    Checked on the data rather than on the constraint, because the constraint
    only covers assignments that are *currently* active; two stays that
    overlapped last month would both be RELEASED and slip past it.
    """

    stays: dict[str, list[tuple] ] = defaultdict(list)
    for assignment in db.scalars(select(BedAssignment)).all():
        stays[str(assignment.location_id)].append(
            (assignment.assigned_at, assignment.released_at)
        )

    for bed_id, periods in stays.items():
        periods.sort(key=lambda item: item[0])
        for (_start, end), (next_start, _next_end) in zip(periods, periods[1:]):
            assert end is not None, f"cama {bed_id} con dos estancias y una abierta"
            assert end <= next_start, f"cama {bed_id} con estancias superpuestas"


def test_time_never_runs_backwards(db: Session) -> None:
    for encounter in db.scalars(select(Encounter)).all():
        if encounter.ended_at is not None:
            assert encounter.ended_at >= encounter.started_at

    for assignment in db.scalars(select(BedAssignment)).all():
        if assignment.released_at is not None:
            assert assignment.released_at >= assignment.assigned_at

    rows = db.execute(
        select(BedRequest.requested_at, BedAssignment.assigned_at).join(
            BedAssignment, BedAssignment.bed_request_id == BedRequest.id
        )
    ).all()
    assert rows, "ninguna solicitud llego a tener asignacion"
    for requested_at, assigned_at in rows:
        assert assigned_at >= requested_at, "cama asignada antes de pedirla"


def test_a_finished_encounter_released_its_bed(db: Session) -> None:
    rows = db.execute(
        select(Encounter.status, BedAssignment.status)
        .join(BedRequest, BedRequest.encounter_id == Encounter.id)
        .join(BedAssignment, BedAssignment.bed_request_id == BedRequest.id)
    ).all()
    for encounter_status, assignment_status in rows:
        if encounter_status == EncounterStatus.FINISHED:
            assert assignment_status == BedAssignmentStatus.RELEASED


def test_a_released_bed_goes_through_cleaning(db: Session) -> None:
    """Release never jumps straight to AVAILABLE.

    An empty dirty bed is not a bed the network can offer, and the gap
    between the two events is the turnaround time it is measured on.
    """

    events = db.scalars(
        select(BedStatusEvent).order_by(BedStatusEvent.event_at)
    ).all()
    by_bed: dict[str, list[BedStatusEvent]] = defaultdict(list)
    for event_row in events:
        by_bed[str(event_row.location_id)].append(event_row)

    seen_cleaning = False
    for history in by_bed.values():
        for previous, current in zip(history, history[1:]):
            if previous.new_status == BedStatus.OCCUPIED:
                assert current.new_status != BedStatus.AVAILABLE, (
                    "una cama paso de ocupada a disponible sin aseo"
                )
            if current.new_status == BedStatus.CLEANING:
                seen_cleaning = True
                assert current.event_at > previous.event_at
    assert seen_cleaning, "el dataset no genero ningun ciclo de aseo"


def test_bed_status_agrees_with_its_own_history(db: Session) -> None:
    """The current status must be reproducible from the events.

    If the two disagree, the audit trail is fiction.
    """

    beds = db.scalars(
        select(Location).where(Location.location_type == LocationType.BED)
    ).all()
    checked = 0
    for bed in beds:
        last = db.scalar(
            select(BedStatusEvent.new_status)
            .where(BedStatusEvent.location_id == bed.id)
            .order_by(BedStatusEvent.event_at.desc())
            .limit(1)
        )
        if last is None:
            continue
        checked += 1
        if bed.status in {BedStatus.BLOCKED, BedStatus.MAINTENANCE}:
            continue
        assert bed.status == last, f"cama {bed.code}: estado {bed.status} vs historia {last}"
    assert checked > 0


def test_a_queue_actually_formed(db: Session) -> None:
    """Scarcity is the problem the project exists to solve.

    A dataset where every request finds a bed immediately would make the
    priority ordering decoration and prove nothing.
    """

    pending = db.scalar(
        select(func.count())
        .select_from(BedRequest)
        .where(BedRequest.status == BedRequestStatus.PENDING)
    )
    total = db.scalar(select(func.count()).select_from(BedRequest))
    assert pending > 0, "ninguna solicitud quedo en espera"
    assert pending < total, "ninguna solicitud consiguio cama"


def test_observations_stay_within_physiological_limits(db: Session) -> None:
    rows = db.execute(
        select(Observation.code, Observation.value_numeric)
    ).all()
    assert rows
    for code, value in rows:
        low, high = PLAUSIBLE_RANGE[code]
        assert low <= float(value) <= high, f"{code}={value}"


def test_imaging_identifiers_are_unique(db: Session) -> None:
    """Two studies sharing a UID would silently merge in any PACS."""

    total = db.scalar(select(func.count()).select_from(ImagingStudy))
    uids = db.scalar(select(func.count(func.distinct(ImagingStudy.study_instance_uid))))
    accessions = db.scalar(
        select(func.count(func.distinct(ImagingStudy.accession_number)))
    )
    assert total == uids == accessions


def test_imaging_belongs_to_a_real_encounter(db: Session) -> None:
    orphans = db.scalar(
        select(func.count())
        .select_from(ImagingStudy)
        .outerjoin(Encounter, Encounter.id == ImagingStudy.encounter_id)
        .where(ImagingStudy.encounter_id.is_not(None))
        .where(Encounter.id.is_(None))
    )
    assert orphans == 0


def test_seeding_twice_produces_the_same_data(db: Session) -> None:
    """Reproducibility is what makes a rehearsed demo safe."""

    documents = db.scalars(
        select(Patient.document_number).order_by(Patient.document_number)
    ).all()
    profiles = db.scalars(
        select(Patient.clinical_profile).order_by(Patient.document_number)
    ).all()

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        seed_module.seed(reset=True, patient_count=PATIENTS)
    finally:
        seed_module.SessionLocal = original

    with factory() as other:
        assert (
            other.scalars(
                select(Patient.document_number).order_by(Patient.document_number)
            ).all()
            == documents
        )
        assert (
            other.scalars(
                select(Patient.clinical_profile).order_by(Patient.document_number)
            ).all()
            == profiles
        )


def _seed_into_memory(patient_count: int) -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        seed_module.seed(reset=True, patient_count=patient_count)
    finally:
        seed_module.SessionLocal = original
    return factory()


def test_growing_the_dataset_does_not_rewrite_the_patients_already_in_it() -> None:
    """Patient 7 must be the same person whether we seed 40 or 200.

    This is what a stream per patient buys. With one shared stream, adding a
    patient shifts every draw after it, so enlarging the dataset would quietly
    replace everybody -- and no result computed on the small run could be
    compared with the large one.
    """

    def roster(count: int) -> list[tuple]:
        with _seed_into_memory(count) as session:
            return session.execute(
                select(
                    Patient.document_number,
                    Patient.first_name,
                    Patient.last_name,
                    Patient.birth_date,
                    Patient.clinical_profile,
                )
                .order_by(Patient.document_number)
                .limit(40)
            ).all()

    assert roster(40) == roster(200)[:40]


def test_the_decision_summary_sees_the_queue(db: Session) -> None:
    """Regression: the waiting queue was grouped by the wrong column.

    A bed request starts with no destination -- it gets one only when the
    coordinator places it -- so grouping the queue by ``target_organization_id``
    counted zero waiting requests for every institution, and the capacity index
    could never fall below the number of free beds. Every IPS was permanently
    reported as able to receive patients, which is precisely the advice that
    makes a coordination system dangerous rather than useless.
    """

    from app.services import analytics

    summary = analytics.decision_summary(db)["organizations"]
    assert summary, "el resumen no devolvio ninguna institucion"
    assert sum(row["waiting_requests"] for row in summary) > 0

    for row in summary:
        expected = row["available_beds"] - row["waiting_requests"]
        assert row["capacity_index"] == expected
        if expected < 0:
            assert row["recommendation"] == "saturada"
        elif expected > 0:
            assert row["recommendation"] == "recibir"
        else:
            assert row["recommendation"] == "al limite"


def test_nobody_in_the_queue_has_been_waiting_for_weeks(db: Session) -> None:
    """Regression: requests that found no bed at first were abandoned.

    They stayed pending forever even after beds freed up, and the live queue
    showed a median wait of 29 days with 20 empty beds. A request with no bed
    at hand has to take the next one that frees up.
    """

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    waits = [
        (now - requested.replace(tzinfo=None)).total_seconds() / 86400
        for requested in db.scalars(
            select(BedRequest.requested_at).where(
                BedRequest.status == BedRequestStatus.PENDING
            )
        )
    ]
    assert waits, "no quedo nadie en la cola"
    assert max(waits) < 3, f"una solicitud lleva {max(waits):.1f} dias esperando"
    assert min(waits) >= 0, "hay solicitudes fechadas en el futuro"


def test_a_bed_is_occupied_exactly_when_it_holds_an_active_assignment(db: Session) -> None:
    """The invariant the board depends on, checked in both directions.

    Regression: bed statuses were re-read from the event history ordered by
    time. Events sharing a timestamp came back in arbitrary order, and 27 beds
    with a patient in them were shown as available -- the double booking this
    project exists to prevent. The older history test did not catch it
    because it read the history the same flawed way.
    """

    held = set(
        db.scalars(
            select(BedAssignment.location_id).where(
                BedAssignment.status == BedAssignmentStatus.ACTIVE
            )
        )
    )
    occupied = set(
        db.scalars(
            select(Location.id).where(
                Location.location_type == LocationType.BED,
                Location.status == BedStatus.OCCUPIED,
            )
        )
    )
    assert held, "ninguna cama quedo ocupada"
    assert held == occupied, (
        f"{len(held - occupied)} camas con paciente figuran libres; "
        f"{len(occupied - held)} ocupadas sin asignacion"
    )


def test_no_two_events_of_a_bed_share_a_timestamp(db: Session) -> None:
    """With a tie, "which state came last" has no answer."""

    duplicated = db.execute(
        select(BedStatusEvent.location_id, BedStatusEvent.event_at)
        .group_by(BedStatusEvent.location_id, BedStatusEvent.event_at)
        .having(func.count() > 1)
    ).all()
    assert duplicated == []
