"""Pydantic models for the traceability endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AuditAction


class RecordVersionRead(BaseModel):
    """One preserved version of a record, as returned by history endpoints."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    snapshot_json: dict[str, Any]
    changed_fields: list[str] | None
    changed_by: uuid.UUID | None
    changed_at: datetime


class AuditLogRead(BaseModel):
    """One audit entry. ``username`` is resolved for readability in demos."""

    id: uuid.UUID
    user_id: uuid.UUID | None
    username: str | None
    action: AuditAction
    entity_type: str
    entity_id: uuid.UUID | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
