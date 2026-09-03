"""Pydantic models for the FHIR integration endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import SyncStatus


class FhirSyncRead(BaseModel):
    """Outcome of one synchronization, mirroring ``fhir_sync_log``."""

    model_config = ConfigDict(from_attributes=True)

    entity_type: str
    entity_id: uuid.UUID
    fhir_resource_type: str
    fhir_resource_id: str | None
    fhir_version_id: str | None
    sync_status: SyncStatus
    last_synced_at: datetime | None
    attempt_count: int
    error_message: str | None
