"""Pydantic models for authentication endpoints."""

from __future__ import annotations

import uuid

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
