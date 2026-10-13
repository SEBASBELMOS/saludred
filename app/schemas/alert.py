"""Alerts (R20) and pending-work counters (R18). Field names follow the public
Spanish contract: ``id``, ``paciente_id``, ``nivel``, ``estado``."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.alert import Alert
from app.models.enums import AlertKind, AlertStatus


class AlertAcknowledge(BaseModel):
    """The note is REQUIRED: acknowledging is a clinical act, not a click."""

    model_config = ConfigDict(extra="forbid")

    nota: str = Field(min_length=1, max_length=1000)

    @field_validator("nota")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("nota must not be blank")
        return value


class AlertRead(BaseModel):
    id: uuid.UUID
    paciente_id: uuid.UUID | None
    nivel: str
    estado: AlertStatus
    kind: AlertKind
    mensaje: str
    created_at: datetime
    reporte_id: uuid.UUID | None = None
    cama_id: uuid.UUID | None = None
    organizacion_id: uuid.UUID | None = None
    destinatario_id: uuid.UUID | None = None
    escalated_at: datetime | None = None
    escalated_to: list[str] | None = None
    reconocida_por: uuid.UUID | None = None
    reconocida_por_nombre: str | None = None
    reconocida_en: datetime | None = None
    nota_reconocimiento: str | None = None
    datos: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, alert: Alert) -> "AlertRead":
        return cls(
            id=alert.id,
            paciente_id=alert.patient_id,
            nivel=alert.nivel,
            estado=alert.status,
            kind=alert.kind,
            mensaje=alert.mensaje,
            created_at=alert.created_at,
            reporte_id=alert.ai_report_id,
            cama_id=alert.bed_id,
            organizacion_id=alert.organization_id,
            destinatario_id=alert.recipient_user_id,
            escalated_at=alert.escalated_at,
            escalated_to=alert.escalated_to,
            reconocida_por=alert.acknowledged_by,
            reconocida_por_nombre=(
                alert.acknowledger.full_name if alert.acknowledger is not None else None
            ),
            reconocida_en=alert.acknowledged_at,
            nota_reconocimiento=alert.nota_reconocimiento,
            datos=alert.datos,
        )


class PendingRead(BaseModel):
    reportes_por_revisar: int
    remisiones_recibidas: int
    alertas_activas: int
