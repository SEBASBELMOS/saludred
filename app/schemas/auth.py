"""Pydantic models for authentication endpoints."""

from __future__ import annotations

import uuid

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RoleCode


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    role: RoleCode


class CurrentUserRead(BaseModel):
    """Identity and scope of the authenticated account, as the API sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    full_name: str
    role: RoleCode
    organization_id: uuid.UUID | None
    patient_id: uuid.UUID | None


class UserAccountRead(BaseModel):
    """Estado de una cuenta, tal como lo ve el administrador."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    full_name: str
    email: str | None
    role: RoleCode
    is_active: bool
    failed_login_attempts: int
    is_locked: bool
    locked_at: datetime | None
    last_login_at: datetime | None


class AccountLockAction(BaseModel):
    """Cuerpo de bloqueo y desbloqueo."""

    reason: str | None = Field(
        default=None,
        max_length=300,
        description="Queda registrado en la auditoria junto con quien lo hizo",
    )


class AccountCreate(BaseModel):
    """Alta de una cuenta por la administracion.

    El vinculo depende del rol: un operador pertenece a una IPS, un coordinador
    a una EPS y un paciente a su ficha, que se indica por numero de documento.
    """

    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9._-]+$")
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    role: RoleCode
    password: str = Field(min_length=8, max_length=128)
    organization_id: uuid.UUID | None = None
    patient_document: str | None = Field(default=None, max_length=32)
