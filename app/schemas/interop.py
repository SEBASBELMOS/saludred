"""Interoperability: the admin views (reconciliation queue, registered partners)."""

from __future__ import annotations

import uuid
from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InteropReferralCreate(BaseModel):
    """A doctor refers a patient to an external institution (R30). No URLs, no secrets."""

    model_config = ConfigDict(extra="forbid")

    paciente_id: uuid.UUID
    cliente_interop_id: str = Field(min_length=3, max_length=80)
    motivo: str = Field(min_length=1, max_length=1000)
    servicio: str = Field(min_length=1, max_length=200)
    prioridad: Literal["rutina", "urgente", "inmediata"] = "rutina"


class InteropReferralRead(BaseModel):
    """An outbound referral and the answer of the partner."""

    id: uuid.UUID
    paciente_id: uuid.UUID
    cliente_interop_id: str
    solicitud_id: str
    estado: str
    motivo: str
    servicio: str
    prioridad: str
    resumen_respuesta: str | None
    creada_en: datetime


class InteropPendingRead(BaseModel):
    """A message that arrived from a partner and waits for an admin (slide 30, the 202)."""

    id: uuid.UUID
    recurso: str
    cliente: str | None
    estado: str
    motivo: str | None
    id_federado: str | None
    resumen: str
    paciente_id: uuid.UUID | None
    recibido_en: datetime
    conciliado_en: datetime | None = None


class InteropReconcile(BaseModel):
    """Which patient (and, for a referral, which specialist) the pending message goes to."""

    model_config = ConfigDict(extra="forbid")

    paciente_id: uuid.UUID | None = None
    especialista_id: uuid.UUID | None = None


class InteropDestinationRead(BaseModel):
    """A partner a doctor can refer to. Only the public name: no scopes, no secrets."""

    client_id: str
    nombre: str


class InteropClientRead(BaseModel):
    """A registered partner. Never carries a secret."""

    client_id: str
    nombre: str
    alcances: list[str]
    activo: bool
    creado_en: datetime
    ultimo_uso: datetime | None
