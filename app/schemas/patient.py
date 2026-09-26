"""Pydantic models for the patient resource."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AdministrativeGender, ClinicalProfile, DocumentType


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
    # Solo lectura: lo asigna el generador de datos sinteticos y sirve como
    # referencia para validar agrupamientos. No se acepta al crear ni al
    # editar, porque no es un dato que registre el personal clinico.
    clinical_profile: ClinicalProfile | None = None
    # Quien registro al paciente. La API decide si alguien puede editarlo; la
    # pantalla lo usa solo para no ofrecer un boton que terminaria en 403.
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
