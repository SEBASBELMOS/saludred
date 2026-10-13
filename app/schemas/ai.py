"""Pydantic models for AI reports and the clinical record export.

Field names follow the public contract (Spanish where the course contract fixes
them: ``estado``, ``observaciones``, ``reportes_ia``); everything else is English.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AiReportKind, AiReportStatus
from app.schemas.observation import ObservationRead
from app.schemas.patient import PatientRead


class AiAnalysisCreate(BaseModel):
    """Optional input of ``POST /patients/{id}/ai-analyses``.

    Everything else the model needs is taken from the patient's record. The ESI
    triage level is not stored anywhere in the system, so the clinician may
    provide it here; without it the model imputes its training median.
    """

    model_config = ConfigDict(extra="forbid")

    triage_level: int | None = Field(default=None, ge=1, le=5, description="ESI, 1 = most urgent")


class AiReportApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observaciones: str | None = Field(default=None, max_length=1000)


class AiReportReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str = Field(min_length=3, max_length=500)
    observaciones: str | None = Field(default=None, max_length=1000)

    @field_validator("motivo")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("motivo must not be blank")
        return value


class AiFeatureContribution(BaseModel):
    feature: str
    value: float | str
    contribution: float
    imputed: bool


class AiReportRead(BaseModel):
    """An AI report without the payload (that has its own endpoint)."""

    id: uuid.UUID
    patient_id: uuid.UUID
    kind: AiReportKind
    estado: AiReportStatus
    pseudonym: str
    probability: float
    nivel: str
    critico: bool
    threshold: float | None
    model_version: str
    imputed: list[str]
    top_features: list[AiFeatureContribution]
    requested_by: uuid.UUID
    reviewed_by: uuid.UUID | None
    firmado_por: str | None = Field(description="Name of the clinician who signed")
    reviewed_at: datetime | None
    observaciones: str | None
    motivo_rechazo: str | None
    solicitud_cama_id: uuid.UUID | None = Field(
        description="Bed request whose criticality was set from this approved report"
    )
    created_at: datetime


class AiPayloadRead(BaseModel):
    """The exact JSON sent to the AI service."""

    payload: dict[str, Any]


class ClinicalRecordReport(BaseModel):
    """An approved report as it appears in the clinical record."""

    id: uuid.UUID
    kind: AiReportKind
    estado: AiReportStatus
    probability: float
    nivel: str
    model_version: str
    top_features: list[AiFeatureContribution]
    observaciones: str | None
    firmado_por: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ClinicalRecordRead(BaseModel):
    paciente: PatientRead
    observaciones: list[ObservationRead]
    reportes_ia: list[ClinicalRecordReport]
    generado_en: datetime
