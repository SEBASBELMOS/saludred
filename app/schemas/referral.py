"""Referrals (R08). Field names follow the public Spanish contract."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ReferralStatus


def _not_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ReferralCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paciente_id: uuid.UUID
    especialista_id: uuid.UUID
    motivo: str = Field(min_length=1, max_length=1000)

    check_motivo = field_validator("motivo")(_not_blank)


class ReferralReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=1, max_length=500)

    check_motivo = field_validator("motivo")(_not_blank)


class ReferralReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    informe: str = Field(min_length=1, max_length=10000)

    check_informe = field_validator("informe")(_not_blank)


class ReferralRead(BaseModel):
    """``id`` and ``paciente_id`` are the contract; the rest feeds the inbox screen."""

    id: uuid.UUID
    paciente_id: uuid.UUID
    paciente_nombre: str
    motivo: str
    estado: ReferralStatus
    remitente_id: uuid.UUID
    remitente_nombre: str
    especialista_id: uuid.UUID
    especialista_nombre: str
    especialista_organizacion: str | None
    motivo_rechazo: str | None
    informe: str | None
    creada_en: datetime
    aceptada_en: datetime | None
    rechazada_en: datetime | None
    completada_en: datetime | None


class SpecialistRead(BaseModel):
    id: uuid.UUID
    nombre: str
    organizacion: str | None
