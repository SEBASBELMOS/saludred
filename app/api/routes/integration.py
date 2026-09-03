"""Integration endpoints: relational rows <-> HAPI FHIR resources.

POST pushes one row (and, transparently, its dependency chain) to the FHIR
server through an idempotent conditional update. GET reads the mirrored
resource back FROM the FHIR server -- not from our database -- which is what
demonstrates the round trip.

ADMIN only: synchronization is a system function, not a clinical one.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.organization import Location
from app.schemas.fhir import FhirSyncRead
from app.services import encounters as encounters_service
from app.services import fhir_sync
from app.services import observations as observations_service
from app.services import organizations as organizations_service
from app.services import patients as patients_service
from app.services.errors import NotFoundError
from app.services.fhir_sync import FhirClient, get_fhir_client

router = APIRouter(prefix="/api/v1/integration/fhir", tags=["integracion FHIR"])

Fhir = Annotated[FhirClient, Depends(get_fhir_client)]


@router.post(
    "/patients/{patient_id}",
    response_model=FhirSyncRead,
    summary="Sincronizar paciente hacia FHIR",
)
def sync_patient(
    db: DbSession, user: CurrentUser, client: Fhir, patient_id: uuid.UUID
) -> FhirSyncRead:
    authz.require_admin(user)
    patient = patients_service.get_patient(db, patient_id)
    return fhir_sync.sync_patient(db, client, patient, actor=user)


@router.get(
    "/patients/{patient_id}",
    summary="Leer el recurso Patient desde el servidor FHIR",
)
def read_patient(
    db: DbSession, user: CurrentUser, client: Fhir, patient_id: uuid.UUID
) -> dict[str, Any]:
    authz.require_admin(user)
    return fhir_sync.read_synced_resource(db, client, "patients", patient_id)


@router.post(
    "/encounters/{encounter_id}",
    response_model=FhirSyncRead,
    summary="Sincronizar encuentro hacia FHIR",
)
def sync_encounter(
    db: DbSession, user: CurrentUser, client: Fhir, encounter_id: uuid.UUID
) -> FhirSyncRead:
    authz.require_admin(user)
    encounter = encounters_service.get_encounter(db, encounter_id)
    return fhir_sync.sync_encounter(db, client, encounter, actor=user)


@router.get(
    "/encounters/{encounter_id}",
    summary="Leer el recurso Encounter desde el servidor FHIR",
)
def read_encounter(
    db: DbSession, user: CurrentUser, client: Fhir, encounter_id: uuid.UUID
) -> dict[str, Any]:
    authz.require_admin(user)
    return fhir_sync.read_synced_resource(db, client, "encounters", encounter_id)


@router.post(
    "/observations/{observation_id}",
    response_model=FhirSyncRead,
    summary="Sincronizar observacion hacia FHIR",
)
def sync_observation(
    db: DbSession, user: CurrentUser, client: Fhir, observation_id: uuid.UUID
) -> FhirSyncRead:
    authz.require_admin(user)
    observation = observations_service.get_observation(db, observation_id)
    return fhir_sync.sync_observation(db, client, observation, actor=user)


@router.get(
    "/observations/{observation_id}",
    summary="Leer el recurso Observation desde el servidor FHIR",
)
def read_observation(
    db: DbSession, user: CurrentUser, client: Fhir, observation_id: uuid.UUID
) -> dict[str, Any]:
    authz.require_admin(user)
    return fhir_sync.read_synced_resource(db, client, "observations", observation_id)


@router.post(
    "/organizations/{organization_id}",
    response_model=FhirSyncRead,
    summary="Sincronizar organizacion hacia FHIR",
)
def sync_organization(
    db: DbSession, user: CurrentUser, client: Fhir, organization_id: uuid.UUID
) -> FhirSyncRead:
    authz.require_admin(user)
    organization = organizations_service.get_organization(db, organization_id)
    return fhir_sync.sync_organization(db, client, organization, actor=user)


@router.get(
    "/organizations/{organization_id}",
    summary="Leer el recurso Organization desde el servidor FHIR",
)
def read_organization(
    db: DbSession, user: CurrentUser, client: Fhir, organization_id: uuid.UUID
) -> dict[str, Any]:
    authz.require_admin(user)
    return fhir_sync.read_synced_resource(db, client, "organizations", organization_id)


@router.post(
    "/locations/{location_id}",
    response_model=FhirSyncRead,
    summary="Sincronizar ubicacion/cama hacia FHIR",
)
def sync_location(
    db: DbSession, user: CurrentUser, client: Fhir, location_id: uuid.UUID
) -> FhirSyncRead:
    authz.require_admin(user)
    location = db.get(Location, location_id)
    if location is None or location.deleted_at is not None:
        raise NotFoundError("Ubicacion no encontrada")
    return fhir_sync.sync_location(db, client, location, actor=user)


@router.get(
    "/locations/{location_id}",
    summary="Leer el recurso Location desde el servidor FHIR",
)
def read_location(
    db: DbSession, user: CurrentUser, client: Fhir, location_id: uuid.UUID
) -> dict[str, Any]:
    authz.require_admin(user)
    return fhir_sync.read_synced_resource(db, client, "locations", location_id)
