"""Pydantic models for the imaging study resource."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ImagingModality, ImagingStudyStatus

# A DICOM UID is a dotted sequence of digits, at most 64 characters. Validating
# the shape here keeps a malformed identifier from reaching the PACS, where it
# would either be rejected or -- worse -- accepted and become impossible to
# look up again.
DICOM_UID = re.compile(r"^\d+(\.\d+)+$")


class ImagingStudyBase(BaseModel):
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None = None
    organization_id: uuid.UUID
    study_instance_uid: str = Field(min_length=5, max_length=64)
    accession_number: str = Field(min_length=1, max_length=40)
    modality: ImagingModality
    status: ImagingStudyStatus = ImagingStudyStatus.REGISTERED
    body_site: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    series_count: int = Field(default=1, ge=1, le=10_000)
    instance_count: int = Field(default=1, ge=1, le=1_000_000)
    started_at: datetime

    @field_validator("study_instance_uid")
    @classmethod
    def check_uid_shape(cls, value: str) -> str:
        if not DICOM_UID.match(value):
            raise ValueError(
                "study_instance_uid must be a dotted numeric DICOM UID"
            )
        return value


class ImagingStudyCreate(ImagingStudyBase):
    """Payload accepted by ``POST /imaging-studies``.

    The study is registered before the pixels exist: ``pacs_study_id`` is
    filled in later, when the images are actually uploaded. Recording the
    metadata first is what lets the network know a study was ordered even if
    the upload never happens.
    """


class ImagingStudyUpdate(BaseModel):
    """Payload accepted by ``PUT /imaging-studies/{id}`` (partial update)."""

    status: ImagingStudyStatus | None = None
    body_site: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=300)
    series_count: int | None = Field(default=None, ge=1, le=10_000)
    instance_count: int | None = Field(default=None, ge=1, le=1_000_000)
    pacs_study_id: str | None = Field(default=None, max_length=120)


class ImagingStudyRead(ImagingStudyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pacs_study_id: str | None = None
    created_at: datetime
    updated_at: datetime
