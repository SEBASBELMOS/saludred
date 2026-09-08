"""Pydantic models for bed capacity and coordination."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    Priority,
)


# ---------------------------------------------------------------------------
# Camas y capacidad
# ---------------------------------------------------------------------------


class BedRead(BaseModel):
    """A physical bed with its operational state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    service: str | None
    status: BedStatus
    organization_id: uuid.UUID
    parent_location_id: uuid.UUID | None


class ServiceCapacity(BaseModel):
    """Bed counts for one service inside an institution."""

    service: str
    total: int
    available: int
    occupied: int
    reserved: int
    cleaning: int
    blocked: int
    maintenance: int


class OrganizationCapacity(BaseModel):
    """Bed counts for one institution, broken down by service.

    ``available`` is the number the coordinator actually acts on: a bed in
    cleaning or maintenance exists but cannot receive a patient right now.
    """

    organization_id: uuid.UUID
    organization_code: str
    organization_name: str
    total: int
    available: int
    occupied: int
    reserved: int
    cleaning: int
    blocked: int
    maintenance: int
    occupancy_rate: float = Field(
        description="Ocupadas sobre el total, entre 0 y 1"
    )
    pending_requests: int = Field(
        description="Solicitudes sin resolver dirigidas a esta institucion"
    )
    services: list[ServiceCapacity]


class NetworkCapacity(BaseModel):
    """Capacity across the whole EPS network.

    This is the view the coordinator needs to answer the question the project
    exists for: where is there a free bed right now.
    """

    total: int
    available: int
    occupied: int
    occupancy_rate: float
    pending_requests: int
    organizations: list[OrganizationCapacity]


# ---------------------------------------------------------------------------
# Solicitudes de cama
# ---------------------------------------------------------------------------


class BedRequestCreate(BaseModel):
    """Payload accepted by ``POST /bed-requests``."""

    encounter_id: uuid.UUID
    required_service: str = Field(min_length=1, max_length=120)
    priority: Priority
    target_organization_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Institucion destino. Puede omitirse: una solicitud nace sin "
            "destino y lo recibe cuando el coordinador la resuelve."
        ),
    )
    notes: str | None = Field(default=None, max_length=500)


class BedRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    encounter_id: uuid.UUID
    requesting_organization_id: uuid.UUID
    target_organization_id: uuid.UUID | None
    required_service: str
    priority: Priority
    status: BedRequestStatus
    notes: str | None
    requested_at: datetime
    resolved_at: datetime | None


class QueuedBedRequest(BedRequestRead):
    """A pending request as it appears in the priority queue."""

    queue_position: int = Field(description="1 es la siguiente en ser atendida")
    patient_name: str
    patient_document: str
    waiting_minutes: int


class BedAssignRequest(BaseModel):
    """Payload accepted by ``POST /bed-requests/{id}/assign``."""

    location_id: uuid.UUID = Field(description="Cama a asignar")
    override_priority: bool = Field(
        default=False,
        description=(
            "Asignar aunque haya solicitudes mas urgentes esperando. Queda "
            "registrado en la auditoria con la justificacion."
        ),
    )
    reason: str | None = Field(
        default=None,
        max_length=300,
        description="Obligatorio cuando se salta el orden de prioridad",
    )


class BedAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bed_request_id: uuid.UUID
    location_id: uuid.UUID
    status: BedAssignmentStatus
    assigned_at: datetime
    released_at: datetime | None


class BedCandidate(BaseModel):
    """A bed that could serve a request, with why it was ranked where it is."""

    location_id: uuid.UUID
    code: str
    name: str
    service: str | None
    organization_id: uuid.UUID
    organization_name: str
    same_service: bool = Field(
        description="La cama pertenece al servicio que la solicitud pide"
    )
    same_organization: bool = Field(
        description="La cama esta en la institucion que hizo la solicitud"
    )


class BedStatusChange(BaseModel):
    """Payload accepted by ``POST /beds/{id}/status``."""

    new_status: BedStatus
    reason: str | None = Field(default=None, max_length=300)


class BedStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    location_id: uuid.UUID
    encounter_id: uuid.UUID | None
    previous_status: BedStatus | None
    new_status: BedStatus
    reason: str | None
    event_at: datetime
    changed_by: uuid.UUID | None
