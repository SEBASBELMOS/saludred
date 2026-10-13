"""Queue ordering by priority, criticality and wait time, and the inventory view.

The queue listing and the priority-conflict rule must agree, so both are
exercised against the same set of requests.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import BedAssignment, BedRequest, Encounter, Location, Organization
from app.models.enums import (
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    LocationType,
    OrganizationType,
    Priority,
)
from app.services import beds as beds_service
from app.services.errors import ConflictError, InvalidValueError
from tests.test_bed_rules import _user, db  # noqa: F401  (fixture reuse)

SERVICE = "Servicio de prueba"


@pytest.fixture
def scene(db: Session):  # noqa: F811
    """A clean queue for one service inside one IPS, plus a free bed there."""

    for open_request in db.scalars(
        select(BedRequest).where(BedRequest.status.in_(beds_service.OPEN_STATUSES))
    ):
        open_request.status = BedRequestStatus.CANCELLED
    db.commit()

    ips = db.scalar(
        select(Organization).where(Organization.organization_type == OrganizationType.IPS)
    )
    encounters = list(
        db.scalars(select(Encounter).where(Encounter.organization_id == ips.id).limit(4))
    )
    assert len(encounters) >= 3
    bed = db.scalar(
        select(Location).where(
            Location.organization_id == ips.id,
            Location.location_type == LocationType.BED,
            Location.status == BedStatus.AVAILABLE,
        )
    )
    assert bed is not None
    return db, ips, encounters, bed


def _request(scene_data, index: int, priority: Priority, score, minutes_ago: int):  # noqa: ANN001
    db, ips, encounters, _bed = scene_data
    solicitud = BedRequest(
        encounter_id=encounters[index].id,
        requesting_organization_id=ips.id,
        target_organization_id=ips.id,
        required_service=SERVICE,
        priority=priority,
        status=BedRequestStatus.PENDING,
        criticality_score=score,
        requested_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db.add(solicitud)
    db.commit()
    return solicitud


def _queue_ids(db: Session) -> list:
    return [row["id"] for row in beds_service.list_queue(db, service=SERVICE)]


def test_higher_criticality_goes_first_within_the_same_priority(scene) -> None:
    older = _request(scene, 0, Priority.URGENT, 0.30, minutes_ago=90)
    newer = _request(scene, 1, Priority.URGENT, 0.90, minutes_ago=10)
    assert _queue_ids(scene[0]) == [newer.id, older.id]


def test_requests_without_score_go_after_scored_ones(scene) -> None:
    unscored = _request(scene, 0, Priority.URGENT, None, minutes_ago=120)
    scored = _request(scene, 1, Priority.URGENT, 0.05, minutes_ago=5)
    assert _queue_ids(scene[0]) == [scored.id, unscored.id]


def test_wait_time_breaks_ties_between_equal_scores(scene) -> None:
    newer = _request(scene, 0, Priority.URGENT, 0.5, minutes_ago=5)
    older = _request(scene, 1, Priority.URGENT, 0.5, minutes_ago=50)
    assert _queue_ids(scene[0]) == [older.id, newer.id]


def test_priority_dominates_criticality(scene) -> None:
    routine = _request(scene, 0, Priority.ROUTINE, 0.99, minutes_ago=200)
    urgent = _request(scene, 1, Priority.URGENT, None, minutes_ago=1)
    assert _queue_ids(scene[0]) == [urgent.id, routine.id]


def test_priority_conflict_follows_the_queue_order(scene) -> None:
    db, _ips, _encounters, bed = scene
    ahead = _request(scene, 0, Priority.URGENT, 0.90, minutes_ago=10)
    behind = _request(scene, 1, Priority.URGENT, 0.20, minutes_ago=90)
    actor = _user(db, "admin")

    assert beds_service._more_urgent_waiting(db, behind).id == ahead.id
    assert beds_service._more_urgent_waiting(db, ahead) is None

    with pytest.raises(ConflictError):
        beds_service.assign_bed(db, behind, bed.id, actor=actor)

    beds_service.assign_bed(
        db,
        behind,
        bed.id,
        actor=actor,
        override_priority=True,
        reason="Cama adecuada solo para este paciente",
    )
    assert behind.status == BedRequestStatus.ASSIGNED


def test_unscored_request_yields_to_a_scored_one_in_the_conflict_rule(scene) -> None:
    db, _ips, _encounters, _bed = scene
    unscored = _request(scene, 0, Priority.URGENT, None, minutes_ago=100)
    scored = _request(scene, 1, Priority.URGENT, 0.10, minutes_ago=1)
    assert beds_service._more_urgent_waiting(db, unscored).id == scored.id
    assert beds_service._more_urgent_waiting(db, scored) is None


def test_queue_exposes_the_score_read_only(scene) -> None:
    _request(scene, 0, Priority.URGENT, 0.75, minutes_ago=1)
    row = beds_service.list_queue(scene[0], service=SERVICE)[0]
    assert row["criticality_score"] == pytest.approx(0.75)


@pytest.mark.parametrize("score", [-0.01, 1.01, 5.0])
def test_set_criticality_score_rejects_values_outside_range(scene, score) -> None:
    solicitud = _request(scene, 0, Priority.URGENT, None, minutes_ago=1)
    with pytest.raises(InvalidValueError):
        beds_service.set_criticality_score(scene[0], solicitud.id, score)


def test_set_criticality_score_stores_a_valid_value(scene) -> None:
    solicitud = _request(scene, 0, Priority.URGENT, None, minutes_ago=1)
    updated = beds_service.set_criticality_score(scene[0], solicitud.id, 0.82)
    assert float(updated.criticality_score) == pytest.approx(0.82)


# ---------------------------------------------------------------------------
# Vista de inventario de camas
# ---------------------------------------------------------------------------


def _load_view_migration():
    path = next(
        Path(__file__).resolve().parents[1].glob(
            "migrations/versions/*bed_inventory_view_and_criticality.py"
        )
    )
    spec = importlib.util.spec_from_file_location("inventory_view_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_view_shows_the_current_patient(db: Session) -> None:  # noqa: F811
    migration = _load_view_migration()
    db.execute(text(migration.INVENTORY_VIEW_SQL))

    beds_count = db.scalar(
        text("SELECT COUNT(*) FROM locations WHERE location_type = 'BED' AND deleted_at IS NULL")
    )
    rows = db.execute(text(f"SELECT * FROM {migration.INVENTORY_VIEW_NAME}")).mappings().all()
    assert len(rows) == beds_count

    active = db.scalar(
        select(BedAssignment).where(BedAssignment.status == BedAssignmentStatus.ACTIVE)
    )
    occupied = next(r for r in rows if r["cama_id"].replace("-", "") == active.location_id.hex)
    assert occupied["paciente_nombre"]
    assert occupied["paciente_documento"]
    assert occupied["ocupada_desde"] is not None

    free = next(r for r in rows if r["estado"] == BedStatus.AVAILABLE.value)
    assert free["paciente_id"] is None
    assert free["paciente_nombre"] is None
    assert free["ocupada_desde"] is None
    assert free["ips_nombre"]
