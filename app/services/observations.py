"""CRUD operations for observations."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinical import Encounter, Observation
from app.models.identity import User
from app.schemas.observation import ObservationCreate, ObservationUpdate
from app.services import soft_ops
from app.services.errors import ConflictError, NotFoundError, commit
from app.services.encounters import get_encounter, require_patient


def list_observations(
    db: Session,
    *,
    page: int,
    page_size: int,
    patient_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> tuple[list[Observation], int]:
    stmt = select(Observation).where(Observation.deleted_at.is_(None))
    if patient_id is not None:
        stmt = stmt.where(Observation.patient_id == patient_id)
    if encounter_id is not None:
        stmt = stmt.where(Observation.encounter_id == encounter_id)
    if organization_id is not None:
        # An observation has no organization of its own; its institutional
        # scope is inherited from the encounter it was taken in.
        stmt = stmt.join(Encounter, Observation.encounter_id == Encounter.id).where(
            Encounter.organization_id == organization_id
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(Observation.observed_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def list_observations_for_encounter(
    db: Session, encounter_id: uuid.UUID
) -> list[Observation]:
    return list(
        db.scalars(
            select(Observation)
            .where(
                Observation.encounter_id == encounter_id,
                Observation.deleted_at.is_(None),
            )
            .order_by(Observation.observed_at.desc())
        )
    )


def get_observation(db: Session, observation_id: uuid.UUID) -> Observation:
    observation = db.scalar(
        select(Observation).where(
            Observation.id == observation_id, Observation.deleted_at.is_(None)
        )
    )
    if observation is None:
        raise NotFoundError("Observacion no encontrada")
    return observation


def create_observation(
    db: Session, data: ObservationCreate, *, actor: User
) -> Observation:
    require_patient(db, data.patient_id)
    get_encounter(db, data.encounter_id)

    observation = Observation(**data.model_dump(), created_by=actor.id)
    db.add(observation)
    db.flush()
    soft_ops.audit_create(db, observation, actor=actor)
    commit(db)
    db.refresh(observation)
    return observation


def update_observation(
    db: Session, observation: Observation, data: ObservationUpdate, *, actor: User
) -> Observation:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return observation

    if "patient_id" in changes and changes["patient_id"] is not None:
        require_patient(db, changes["patient_id"])
    if "encounter_id" in changes and changes["encounter_id"] is not None:
        get_encounter(db, changes["encounter_id"])

    soft_ops.snapshot_before_update(
        db, observation, actor=actor, changed_fields=sorted(changes)
    )
    for field, value in changes.items():
        setattr(observation, field, value)
    observation.updated_by = actor.id

    _normalize_value(observation)
    commit(db)
    db.refresh(observation)
    return observation


def soft_delete_observation(
    db: Session, observation: Observation, *, actor: User
) -> None:
    soft_ops.soft_delete(db, observation, actor=actor)


def _normalize_value(observation: Observation) -> None:
    """Keep numeric and text mutually exclusive, mirroring the CHECKs."""

    has_numeric = observation.value_numeric is not None
    has_text = (
        observation.value_text is not None and observation.value_text.strip() != ""
    )
    if has_numeric == has_text:
        raise ConflictError(
            "Se requiere exactamente uno entre value_numeric y value_text"
        )
    if has_numeric:
        observation.value_text = None
        if not observation.unit:
            raise ConflictError("unit es obligatorio cuando hay value_numeric")
    else:
        observation.value_numeric = None
