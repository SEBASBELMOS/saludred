"""Pydantic models for the observation resource."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.enums import ObservationStatus


class ObservationBase(BaseModel):
    """Fields shared by create, update and read models."""

    patient_id: uuid.UUID
    encounter_id: uuid.UUID
    status: ObservationStatus = ObservationStatus.FINAL
    code_system: str = Field(default="http://loinc.org", max_length=200)
    code: str = Field(min_length=1, max_length=40)
    display: str = Field(min_length=1, max_length=200)
    value_numeric: Decimal | None = None
    value_text: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=40)
    unit_system: str = Field(default="http://unitsofmeasure.org", max_length=200)
    observed_at: datetime

    @model_validator(mode="after")
    def check_value_present(self) -> "ObservationBase":
        """Mirror the ``has_value`` CHECK: numeric and text are XOR."""

        has_numeric = self.value_numeric is not None
        has_text = self.value_text is not None and self.value_text.strip() != ""
        if has_numeric == has_text:
            raise ValueError("exactly one of value_numeric or value_text is required")
        if has_numeric and not self.unit:
            raise ValueError("unit is required when value_numeric is provided")
        return self

    @field_serializer("value_numeric")
    def serialize_numeric(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None


class ObservationCreate(ObservationBase):
    """Payload accepted by ``POST /observations``."""


class ObservationUpdate(BaseModel):
    """Payload accepted by ``PUT /observations/{id}`` (partial update)."""

    patient_id: uuid.UUID | None = None
    encounter_id: uuid.UUID | None = None
    status: ObservationStatus | None = None
    code_system: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, min_length=1, max_length=40)
    display: str | None = Field(default=None, min_length=1, max_length=200)
    value_numeric: Decimal | None = None
    value_text: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=40)
    unit_system: str | None = Field(default=None, max_length=200)
    observed_at: datetime | None = None


class ObservationRead(ObservationBase):
    """Response returned by observation endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    @field_serializer("value_numeric")
    def serialize_numeric(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None
