"""Consents (R32). Field names follow the public Spanish contract."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.models.enums import ConsentStatus


class ConsentCreate(BaseModel):
    """Register a consent for one patient and EXACTLY one destination.

    ``cliente_interop_id`` is the business ``client_id`` of the partner system;
    ``organizacion_id`` is the in-network organization. Exactly one of the two
    must be set (the service rejects both/neither with 422 before persistence).
    """

    model_config = ConfigDict(extra="forbid")

    paciente_id: uuid.UUID
    organizacion_id: uuid.UUID | None = None
    cliente_interop_id: str | None = None
    vigente_desde: AwareDatetime
    vigente_hasta: AwareDatetime | None = None


class ConsentRead(BaseModel):
    """The public view: ``estado`` is DERIVED from the stored facts, never stored."""

    id: uuid.UUID
    paciente_id: uuid.UUID
    organizacion_id: uuid.UUID | None
    cliente_interop_id: str | None
    estado: ConsentStatus
    vigente_desde: datetime
    vigente_hasta: datetime | None
    creado_en: datetime
    creado_por: uuid.UUID | None
    revocado_en: datetime | None
    revocado_por: uuid.UUID | None
