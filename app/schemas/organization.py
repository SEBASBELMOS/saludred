"""Pydantic models for the organization resource."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import OrganizationType


class OrganizationBase(BaseModel):
    """Fields shared by create, update and read models."""

    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    organization_type: OrganizationType
    parent_organization_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def check_hierarchy(self) -> "OrganizationBase":
        """Mirror the ``hierarchy`` CHECK: EPS has no parent, IPS needs one."""

        if self.organization_type == OrganizationType.EPS:
            if self.parent_organization_id is not None:
                raise ValueError("an EPS must not reference a parent organization")
        elif self.parent_organization_id is None:
            raise ValueError("an IPS must reference a parent EPS")
        return self


class OrganizationCreate(OrganizationBase):
    """Payload accepted by ``POST /organizations``."""


class OrganizationUpdate(BaseModel):
    """Payload accepted by ``PUT /organizations/{id}`` (partial update).

    The hierarchy rule is validated by the service against the merged state,
    because each field is optional on its own.
    """

    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    organization_type: OrganizationType | None = None
    parent_organization_id: uuid.UUID | None = None


class OrganizationRead(OrganizationBase):
    """Response returned by organization endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
