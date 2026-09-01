"""Pydantic models for the patient resource."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AdministrativeGender, DocumentType


class PatientBase(BaseModel):
    """Fields shared by create, update and read models."""

    document_type: DocumentType
    document_number: str = Field(min_length=4, max_length=32)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    birth_date: date
    gender: AdministrativeGender
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    eps_organization_id: uuid.UUID

    @field_validator("document_number")
    @classmethod
    def strip_document_number(cls, value: str) -> str:
        """Reject whitespace-only values and drop accidental padding."""

        value = value.strip()
        if not value:
            raise ValueError("document_number must not be empty")
        return value


class PatientCreate(PatientBase):
    """Payload accepted by ``POST /patients``."""


class PatientUpdate(BaseModel):
    """Payload accepted by ``PUT /patients/{id}``.

    Every field is optional: only the supplied ones are changed, which is what
    later allows the soft-edit history to record exactly which fields moved.
    """

    document_type: DocumentType | None = None
    document_number: str | None = Field(default=None, min_length=4, max_length=32)
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = Field(default=None, min_length=1, max_length=120)
    birth_date: date | None = None
    gender: AdministrativeGender | None = None
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    eps_organization_id: uuid.UUID | None = None

    @field_validator("document_number")
    @classmethod
    def strip_document_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("document_number must not be empty")
        return value


class PatientRead(PatientBase):
    """Response returned by patient endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
