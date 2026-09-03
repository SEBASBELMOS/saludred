"""Traceability: audit entries, edit history snapshots and restoration.

This module is the single writer for ``audit_log`` and ``record_versions``.
Services call it instead of touching those tables directly, so the shape of the
trail stays uniform no matter which entity produced it.

Nothing here commits. The caller owns the transaction, which guarantees that a
snapshot, its audit entry and the change they describe land atomically -- a
history row describing a change that was rolled back would be worse than no
history at all.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.enums import AuditAction
from app.models.governance import AuditLog, RecordVersion
from app.models.identity import User


def snapshot_row(entity: Base) -> dict[str, Any]:
    """Serialize the current column values of a row into a JSON-safe dict.

    Only mapped columns are captured -- relationships would drag in unrelated
    rows and make the snapshot unbounded. Types that JSON cannot carry are
    stringified; ``Decimal`` keeps its textual form so no precision is lost.
    """

    result: dict[str, Any] = {}
    for attr in inspect(entity).mapper.column_attrs:
        value = getattr(entity, attr.key)
        if isinstance(value, (uuid.UUID, datetime, date)):
            result[attr.key] = str(value)
        elif isinstance(value, Decimal):
            result[attr.key] = str(value)
        elif isinstance(value, Enum):
            result[attr.key] = value.value
        else:
            result[attr.key] = value
    return result


def record_version(
    db: Session,
    entity: Base,
    *,
    actor: User,
    changed_fields: list[str],
) -> RecordVersion:
    """Store the pre-change state of a row.

    Must be called BEFORE mutating the entity: what this table holds is the
    value that is about to disappear, which is exactly what a soft edit promises
    to preserve.
    """

    entity_type = entity.__tablename__
    entity_id = entity.id  # type: ignore[attr-defined]
    current_max = db.scalar(
        select(func.max(RecordVersion.version_number)).where(
            RecordVersion.entity_type == entity_type,
            RecordVersion.entity_id == entity_id,
        )
    )
    version = RecordVersion(
        entity_type=entity_type,
        entity_id=entity_id,
        version_number=(current_max or 0) + 1,
        snapshot_json=snapshot_row(entity),
        changed_fields=changed_fields,
        changed_by=actor.id,
    )
    db.add(version)
    return version


def audit(
    db: Session,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor: User | None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit entry.

    ``actor`` is ``None`` only for events that have no authenticated user, such
    as a failed login attempt -- which is precisely an event worth auditing.
    """

    entry = AuditLog(
        user_id=actor.id if actor is not None else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_json=metadata,
    )
    db.add(entry)
    return entry


def mark_deleted(entity: Base, actor: User) -> None:
    """Stamp the soft-delete columns on an entity."""

    entity.deleted_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    entity.deleted_by = actor.id  # type: ignore[attr-defined]


def mark_restored(entity: Base, actor: User) -> None:
    """Clear the deletion mark, keeping the evidence of who deleted it.

    ``deleted_by`` is intentionally left untouched: the restoration columns are
    separate precisely so an undelete never erases who deleted the row first.
    Only ``deleted_at`` returns to NULL, because that column alone decides
    whether the row is live.
    """

    entity.deleted_at = None  # type: ignore[attr-defined]
    entity.restored_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    entity.restored_by = actor.id  # type: ignore[attr-defined]
