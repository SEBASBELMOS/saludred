"""Reactors: small functions that consume a :class:`DomainEvent`.

Today there is one, the notification reactor. Epic 7 adds more (billing on
report approval, critical alerts) by registering them in the same way.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.events.bus import DomainEvent
from app.events.channel import NON_CLINICAL_ROLES, registry
from app.models.identity import User
from app.models.notification import Notification
from app.schemas.notification import NotificationRead

logger = logging.getLogger(__name__)


def recipients_statement(event: DomainEvent) -> Select[tuple[User]]:
    """Users that may actually receive the event.

    Active accounts only. A clinical event also drops the ``paciente`` and
    ``contable`` roles, even when a publisher listed them by mistake.
    """

    stmt = (
        select(User)
        .options(joinedload(User.role))
        .where(User.id.in_(event.recipients), User.is_active.is_(True))
    )
    return stmt


def _push_only(event: DomainEvent, users: list[User]) -> None:
    """Live-only delivery: same JSON shape as a notification, but no inbox row."""

    payload = {
        "id": str(uuid.uuid4()),
        "tipo": event.tipo,
        "mensaje": event.mensaje,
        "leida": True,
        "fecha": event.fecha.isoformat(),
        "paciente_id": str(event.paciente_id) if event.paciente_id else None,
        "datos": event.datos or None,
    }
    for user in users:
        try:
            registry.push(user.id, payload, clinical=event.clinical)
        except Exception:  # noqa: BLE001
            logger.exception("Live push failed for user %s", user.id)


def notification_reactor(event: DomainEvent, db: Session) -> None:
    """Persist one inbox row per recipient, then push it to live connections."""

    if not event.recipients:
        return

    users = list(db.scalars(recipients_statement(event)).unique())
    if event.clinical:
        users = [u for u in users if u.role.code not in NON_CLINICAL_ROLES]
    if not users:
        return

    if not event.persist:
        _push_only(event, users)
        return

    rows = [
        Notification(
            user_id=user.id,
            kind=event.tipo,
            message=event.mensaje,
            patient_id=event.paciente_id,
            data=event.datos or None,
            is_read=False,
            created_at=event.fecha,
        )
        for user in users
    ]
    db.add_all(rows)
    db.commit()

    # The inbox is committed: from here on the push is best effort.
    for user, row in zip(users, rows, strict=True):
        payload = NotificationRead.from_row(row).model_dump(mode="json")
        try:
            registry.push(user.id, payload, clinical=event.clinical)
        except Exception:  # noqa: BLE001
            logger.exception("Live push failed for user %s", user.id)

