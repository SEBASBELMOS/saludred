"""Notification inbox (R18). Field names follow the public Spanish contract."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.notification import Notification


class NotificationRead(BaseModel):
    """``{"id","tipo","mensaje","leida","fecha"}`` plus the identifiers of the fact.

    The same shape travels over the live channel, so the front end handles one
    format whether the notification arrives by SSE or by reading the inbox.
    """

    id: uuid.UUID
    tipo: str
    mensaje: str
    leida: bool
    fecha: datetime
    paciente_id: uuid.UUID | None = None
    datos: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: Notification) -> "NotificationRead":
        return cls(
            id=row.id,
            tipo=row.kind,
            mensaje=row.message,
            leida=row.is_read,
            fecha=row.created_at,
            paciente_id=row.patient_id,
            datos=row.data,
        )
