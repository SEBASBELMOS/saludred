"""Pydantic models for the encounter resource."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import EncounterClass, EncounterStatus, Priority


class EncounterBase(BaseModel):
    """Fields shared by create, update and read models."""

    patient_id: uuid.UUID
    organization_id: uuid.UUID
    location_id: uuid.UUID | None = None
    encounter_class: EncounterClass
    status: EncounterStatus
    priority: Priority = Priority.ROUTINE
    reason_text: str | None = Field(default=None, max_length=500)
    started_at: datetime
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def check_period_order(self) -> "EncounterBase":
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not be earlier than started_at")
        return self


class EncounterCreate(EncounterBase):
    """Payload accepted by ``POST /encounters``."""


class EncounterUpdate(BaseModel):
    """Payload accepted by ``PUT /encounters/{id}`` (partial update)."""

    patient_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    encounter_class: EncounterClass | None = None
    status: EncounterStatus | None = None
    priority: Priority | None = None
    reason_text: str | None = Field(default=None, max_length=500)
    started_at: datetime | None = None
    ended_at: datetime | None = None


class EncounterRead(EncounterBase):
    """Response returned by encounter endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
