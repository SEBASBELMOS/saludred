"""Pydantic models for bed cleaning evidence."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.cleaning import CHECKLIST_ITEMS
from app.models.enums import (
    CleaningDisinfectant,
    CleaningIsolation,
    CleaningReviewStatus,
)

_CHECKLIST_DOC = "Todos los items deben venir en true: " + ", ".join(
    f"`{key}` ({label})" for key, label in CHECKLIST_ITEMS.items()
)


class CleaningRecordCreate(BaseModel):
    started_at: datetime
    finished_at: datetime
    isolation: CleaningIsolation = CleaningIsolation.NONE
    disinfectant: CleaningDisinfectant
    curtains_changed: bool = False
    checklist: dict[str, bool] = Field(description=_CHECKLIST_DOC)
    notes: str | None = Field(default=None, max_length=500)


class CleaningReview(BaseModel):
    approved: bool
    review_notes: str | None = Field(default=None, max_length=300)


class CleaningRecordRead(BaseModel):
    """A cleaning record. The photo bytes are served by their own endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    location_id: uuid.UUID
    started_at: datetime
    finished_at: datetime
    isolation: CleaningIsolation
    disinfectant: CleaningDisinfectant
    curtains_changed: bool
    checklist: dict[str, bool]
    notes: str | None
    has_photo: bool
    photo_content_type: str | None
    photo_size: int | None
    status: CleaningReviewStatus
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    review_notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    # AI guess made when the bed entered CLEANING; null if the service was down.
    predicted_delay_probability: Decimal | None = None
    predicted_delay: bool | None = None
    delay_model_version: str | None = None

    @field_serializer("predicted_delay_probability")
    def _probability(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None


class OverdueCleaningBed(BaseModel):
    """A bed stuck in CLEANING with no approved evidence: free but not cleaned."""

    location_id: uuid.UUID
    code: str
    name: str
    service: str | None
    organization_id: uuid.UUID
    cleaning_since: datetime
    minutes_in_cleaning: int
    pending_review_records: int
    predicted_delay_probability: Decimal | None = None
    predicted_delay: bool | None = None

    @field_serializer("predicted_delay_probability")
    def _probability(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None
