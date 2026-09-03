"""CRUD operations for encounters."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinical import Encounter
from app.models.enums import EncounterStatus
from app.models.identity import User
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.schemas.encounter import EncounterCreate, EncounterUpdate
from app.services import soft_ops
from app.services.errors import ConflictError, NotFoundError, commit


def list_encounters(
    db: Session,
    *,
    page: int,
    page_size: int,
    patient_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    status: EncounterStatus | None = None,
) -> tuple[list[Encounter], int]:
    stmt = select(Encounter).where(Encounter.deleted_at.is_(None))
    if patient_id is not None:
        stmt = stmt.where(Encounter.patient_id == patient_id)
    if organization_id is not None:
        stmt = stmt.where(Encounter.organization_id == organization_id)
    if status is not None:
        stmt = stmt.where(Encounter.status == status)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(Encounter.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def list_encounters_for_patient(
    db: Session,
    patient_id: uuid.UUID,
    *,
    organization_id: uuid.UUID | None = None,
) -> list[Encounter]:
    """List a patient's live encounters.

    ``organization_id`` is the institutional scope filter: a clinical operator
    receives only the encounters of its own IPS, applied inside the query so
    the restriction cannot be bypassed by pagination tricks.
    """

    stmt = select(Encounter).where(
        Encounter.patient_id == patient_id, Encounter.deleted_at.is_(None)
    )
    if organization_id is not None:
        stmt = stmt.where(Encounter.organization_id == organization_id)
    return list(db.scalars(stmt.order_by(Encounter.started_at.desc())))


def get_encounter(db: Session, encounter_id: uuid.UUID) -> Encounter:
    encounter = db.scalar(
        select(Encounter).where(
            Encounter.id == encounter_id, Encounter.deleted_at.is_(None)
        )
    )
    if encounter is None:
        raise NotFoundError("Encuentro no encontrado")
    return encounter


def create_encounter(db: Session, data: EncounterCreate, *, actor: User) -> Encounter:
    require_patient(db, data.patient_id)
    _require_organization(db, data.organization_id)
    _require_location(db, data.location_id, organization_id=data.organization_id)

    encounter = Encounter(**data.model_dump(), created_by=actor.id)
    db.add(encounter)
    db.flush()
    soft_ops.audit_create(db, encounter, actor=actor)
    commit(db)
    db.refresh(encounter)
    return encounter


def update_encounter(
    db: Session, encounter: Encounter, data: EncounterUpdate, *, actor: User
) -> Encounter:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return encounter

    if "patient_id" in changes and changes["patient_id"] is not None:
        require_patient(db, changes["patient_id"])
    if "organization_id" in changes and changes["organization_id"] is not None:
        _require_organization(db, changes["organization_id"])
    if "location_id" in changes:
        _require_location(
            db,
            changes["location_id"],
            organization_id=changes.get("organization_id", encounter.organization_id),
        )

    soft_ops.snapshot_before_update(
        db, encounter, actor=actor, changed_fields=sorted(changes)
    )
    for field, value in changes.items():
        setattr(encounter, field, value)
    encounter.updated_by = actor.id

    _validate_period(encounter.started_at, encounter.ended_at)
    commit(db)
    db.refresh(encounter)
    return encounter


def soft_delete_encounter(db: Session, encounter: Encounter, *, actor: User) -> None:
    soft_ops.soft_delete(db, encounter, actor=actor)


def require_patient(db: Session, patient_id: uuid.UUID) -> None:
    exists = db.scalar(
        select(Patient.id).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if exists is None:
        raise NotFoundError("Paciente referenciado no encontrado")


def _require_organization(db: Session, organization_id: uuid.UUID) -> None:
    exists = db.scalar(
        select(Organization.id).where(
            Organization.id == organization_id, Organization.deleted_at.is_(None)
        )
    )
    if exists is None:
        raise NotFoundError("Organizacion referenciada no encontrada")


def _require_location(
    db: Session, location_id: uuid.UUID | None, *, organization_id: uuid.UUID
) -> None:
    if location_id is None:
        return
    location = db.scalar(
        select(Location).where(
            Location.id == location_id, Location.deleted_at.is_(None)
        )
    )
    if location is None:
        raise NotFoundError("Ubicacion referenciada no encontrada")
    if location.organization_id != organization_id:
        raise ConflictError("La ubicacion no pertenece a la organizacion del encuentro")


def _validate_period(started_at: datetime, ended_at: datetime | None) -> None:
    if ended_at is not None and ended_at < started_at:
        raise ConflictError("ended_at no puede ser anterior a started_at")
