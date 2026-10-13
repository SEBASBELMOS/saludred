"""Notification inbox: list and mark as read. Always scoped to the owner."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.notification import Notification
from app.models.patient import Patient
from app.services.errors import NotFoundError, commit

MAX_LIST = 200


def _of_deleted_patient():  # noqa: ANN202
    """Correlated EXISTS: the notification's patient is soft-deleted."""

    return (
        select(Patient.id)
        .where(Patient.id == Notification.patient_id, Patient.deleted_at.is_not(None))
        .exists()
    )


def inbox_stmt(user: User, *, read: bool | None = None) -> Select[tuple[Notification]]:
    """The inbox of ``user``.

    Rows about a soft-deleted patient stay in the table as history but leave the
    inbox. A notification with no patient (``patient_id IS NULL``) has no
    patient to be deleted, so ``NOT EXISTS`` keeps it, which an inner join would not.
    """

    stmt = select(Notification).where(
        Notification.user_id == user.id, ~_of_deleted_patient()
    )
    if read is not None:
        stmt = stmt.where(Notification.is_read.is_(read))
    return stmt


def count_unread(db: Session, user: User) -> int:
    """Unread notifications that are visible in the inbox."""

    stmt = inbox_stmt(user, read=False).with_only_columns(func.count(Notification.id))
    return db.scalar(stmt) or 0


def list_for_user(
    db: Session, user: User, *, read: bool | None = None, limit: int = 100
) -> list[Notification]:
    stmt = inbox_stmt(user, read=read)
    return list(
        db.scalars(
            stmt.order_by(Notification.created_at.desc(), Notification.id)
            .limit(min(limit, MAX_LIST))
        )
    )


def mark_read(db: Session, user: User, notification_id: uuid.UUID) -> Notification:
    """Mark one notification as read.

    Someone else's notification answers 404, exactly like one that does not
    exist: a 403 would confirm that the id is real.
    """

    row = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if row is None:
        raise NotFoundError("Notificacion no encontrada")
    if not row.is_read:
        row.is_read = True
        commit(db)
    return row
