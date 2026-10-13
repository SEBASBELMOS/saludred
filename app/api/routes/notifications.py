"""Notification inbox (R18).

Every role has an inbox, and only its owner sees or marks its notifications.
Someone else's notification is a 404, not a 403.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.notification import NotificationRead
from app.services import notifications as notifications_service

router = APIRouter(prefix="/api/v1/notifications", tags=["notificaciones"])


@router.get(
    "",
    response_model=list[NotificationRead],
    summary="Bandeja de notificaciones propia (la mas reciente primero)",
)
def list_notifications(
    db: DbSession,
    user: CurrentUser,
    leida: bool | None = Query(default=None, description="false = solo las no leidas"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[NotificationRead]:
    rows = notifications_service.list_for_user(db, user, read=leida, limit=limit)
    return [NotificationRead.from_row(r) for r in rows]


@router.post(
    "/{notification_id}/read",
    response_model=NotificationRead,
    summary="Marcar una notificacion propia como leida",
)
def read_notification(
    db: DbSession, user: CurrentUser, notification_id: uuid.UUID
) -> NotificationRead:
    return NotificationRead.from_row(
        notifications_service.mark_read(db, user, notification_id)
    )
