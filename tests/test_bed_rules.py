"""Bed coordination rules the interface depends on.

Built on the synthetic dataset in SQLite in memory. The route-level checks go
through the real FastAPI app with the database and the authenticated user
swapped in, so they exercise the authorization exactly as a request would.
"""

from __future__ import annotations

import contextlib
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.seed as seed_module
from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import Base, BedAssignment, BedRequest, Encounter, Location, User
from app.models.enums import (
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    LocationType,
)
from app.services import beds as beds_service
from app.services.errors import ConflictError


@pytest.fixture
def db() -> Session:
    # StaticPool: una sola conexion compartida, para que la sesion del test y
    # la que usa la app a traves de TestClient vean la misma base en memoria.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            seed_module.seed(reset=True, patient_count=40)
    finally:
        seed_module.SessionLocal = original

    with factory() as session:
        yield session


def _user(db: Session, username: str) -> User:
    return db.scalar(select(User).where(User.username == username))


@pytest.fixture
def client_as(db: Session):
    """TestClient authenticated as the given seeded account."""

    def build(username: str) -> TestClient:
        account = _user(db, username)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: account
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def _occupied_bed(db: Session) -> Location:
    bed = db.scalar(
        select(Location)
        .join(BedAssignment, BedAssignment.location_id == Location.id)
        .where(BedAssignment.status == BedAssignmentStatus.ACTIVE)
        .limit(1)
    )
    assert bed is not None, "el dataset no dejo ninguna cama ocupada"
    return bed


def test_an_occupied_bed_cannot_be_freed_by_changing_its_status(db: Session) -> None:
    """Otherwise its assignment stays open behind a bed the board shows free,
    and the next assignment double-books it."""

    bed = _occupied_bed(db)
    operator = _user(db, "admin")
    with pytest.raises(ConflictError):
        beds_service.change_bed_status(db, bed, BedStatus.AVAILABLE, actor=operator)


def test_releasing_by_bed_sends_it_to_cleaning(db: Session) -> None:
    bed = _occupied_bed(db)
    assignment = beds_service.active_assignment_for_bed(db, bed.id)
    beds_service.release_bed(db, assignment, actor=_user(db, "admin"))

    db.refresh(bed)
    db.refresh(assignment)
    assert bed.status == BedStatus.CLEANING
    assert assignment.status == BedAssignmentStatus.RELEASED


def test_a_free_bed_has_nothing_to_release(db: Session) -> None:
    free = db.scalar(
        select(Location).where(
            Location.location_type == LocationType.BED,
            Location.status == BedStatus.AVAILABLE,
        )
    )
    with pytest.raises(ConflictError):
        beds_service.active_assignment_for_bed(db, free.id)


def test_capacity_counts_requests_that_have_no_destination_yet(db: Session) -> None:
    """Regression: pending requests were counted by destination only, and a
    request has no destination until the coordinator places it."""

    open_requests = db.scalar(
        select(func.count())
        .select_from(BedRequest)
        .where(BedRequest.status.in_(beds_service.OPEN_STATUSES))
    )
    assert open_requests > 0
    assert beds_service.network_capacity(db)["pending_requests"] == open_requests


def _encounter_without_open_request(db: Session, organization_id) -> Encounter:  # noqa: ANN001
    abiertas = select(BedRequest.encounter_id).where(
        BedRequest.status.in_(beds_service.OPEN_STATUSES)
    )
    encounter = db.scalar(
        select(Encounter)
        .where(Encounter.organization_id == organization_id)
        .where(Encounter.id.not_in(abiertas))
        .limit(1)
    )
    assert encounter is not None
    return encounter


def test_an_operator_cannot_request_a_bed_for_another_ips(db: Session, client_as) -> None:
    operator = _user(db, "operador.norte")
    other = _user(db, "operador.sur")
    foreign = _encounter_without_open_request(db, other.organization_id)

    response = client_as("operador.norte").post(
        "/api/v1/bed-requests",
        json={
            "encounter_id": str(foreign.id),
            "required_service": "Hospitalizacion",
            "priority": "URGENT",
        },
    )
    assert response.status_code == 403, response.text
    assert operator.organization_id != foreign.organization_id


def test_an_operator_can_request_a_bed_for_their_own_ips(db: Session, client_as) -> None:
    operator = _user(db, "operador.norte")
    own = _encounter_without_open_request(db, operator.organization_id)

    response = client_as("operador.norte").post(
        "/api/v1/bed-requests",
        json={
            "encounter_id": str(own.id),
            "required_service": "Hospitalizacion",
            "priority": "URGENT",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == BedRequestStatus.PENDING.value


def test_the_release_route_frees_the_bed_it_is_given(db: Session, client_as) -> None:
    bed = _occupied_bed(db)
    response = client_as("coordinador.eps").post(f"/api/v1/beds/{bed.id}/release")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == BedAssignmentStatus.RELEASED.value

    db.expire_all()
    assert db.get(Location, bed.id).status == BedStatus.CLEANING


def test_an_operator_cannot_release_beds(db: Session, client_as) -> None:
    """Releasing belongs to the coordination, which sees the whole network."""

    bed = _occupied_bed(db)
    response = client_as("operador.norte").post(f"/api/v1/beds/{bed.id}/release")
    assert response.status_code == 403


def test_patients_expose_their_clinical_profile(db: Session, client_as) -> None:
    response = client_as("admin").get("/api/v1/patients?name=ram")
    assert response.status_code == 200, response.text
    assert all(item["clinical_profile"] for item in response.json()["items"])
