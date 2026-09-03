"""Generic soft-operation flows shared by every soft-deletable entity.

Restore and history behave identically for patients, encounters, observations
and organizations, so they are written once. Each entity's service keeps only
what is genuinely its own: validation rules and relationships.
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.enums import AuditAction
from app.models.governance import RecordVersion
from app.models.identity import User
from app.services import trail
from app.services.errors import ConflictError, NotFoundError, commit

EntityT = TypeVar("EntityT", bound=Base)


def get_including_deleted(
    db: Session, model: type[EntityT], entity_id: uuid.UUID, *, label: str
) -> EntityT:
    """Fetch a row regardless of its deletion state.

    This is the lookup used by restore: a query that filters out deleted rows
    can, by definition, never find the row that needs restoring.
    """

    entity = db.scalar(select(model).where(model.id == entity_id))
    if entity is None:
        raise NotFoundError(f"{label} no encontrado")
    return entity


def restore(db: Session, entity: Base, *, actor: User) -> None:
    """Bring a soft-deleted row back to life.

    The role check (ADMIN only) happens at the route via authz; this function
    enforces the state rule that is true regardless of role: restoring a live
    row is a client error, not a no-op, because it almost always means the
    caller is operating on the wrong record.
    """

    if entity.deleted_at is None:  # type: ignore[attr-defined]
        raise ConflictError("El registro no esta eliminado; no hay nada que restaurar")

    trail.mark_restored(entity, actor)
    trail.audit(
        db,
        action=AuditAction.RESTORE,
        entity_type=entity.__tablename__,
        entity_id=entity.id,  # type: ignore[attr-defined]
        actor=actor,
    )
    commit(db)
    db.refresh(entity)


def soft_delete(db: Session, entity: Base, *, actor: User) -> None:
    """Mark a row as deleted and leave the audit evidence, atomically."""

    trail.mark_deleted(entity, actor)
    trail.audit(
        db,
        action=AuditAction.SOFT_DELETE,
        entity_type=entity.__tablename__,
        entity_id=entity.id,  # type: ignore[attr-defined]
        actor=actor,
    )
    commit(db)


def snapshot_before_update(
    db: Session, entity: Base, *, actor: User, changed_fields: list[str]
) -> None:
    """Preserve the pre-change state and audit the edit.

    Call BEFORE mutating the entity. Both rows join the caller's transaction,
    so the history entry exists exactly when the change it describes does.
    """

    trail.record_version(db, entity, actor=actor, changed_fields=changed_fields)
    trail.audit(
        db,
        action=AuditAction.SOFT_EDIT,
        entity_type=entity.__tablename__,
        entity_id=entity.id,  # type: ignore[attr-defined]
        actor=actor,
        metadata={"changed_fields": changed_fields},
    )


def audit_create(db: Session, entity: Base, *, actor: User) -> None:
    """Audit a creation inside the caller's transaction."""

    trail.audit(
        db,
        action=AuditAction.CREATE,
        entity_type=entity.__tablename__,
        entity_id=entity.id,  # type: ignore[attr-defined]
        actor=actor,
    )


def list_history(
    db: Session, model: type[Base], entity_id: uuid.UUID
) -> list[RecordVersion]:
    """Return the saved versions of one row, newest first."""

    return list(
        db.scalars(
            select(RecordVersion)
            .where(
                RecordVersion.entity_type == model.__tablename__,
                RecordVersion.entity_id == entity_id,
            )
            .order_by(RecordVersion.version_number.desc())
        )
    )
