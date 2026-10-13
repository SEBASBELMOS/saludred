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

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import AuditAction, RoleCode
from app.models.organization import Location
from app.schemas.fhir import FhirSyncRead
from app.services import encounters as encounters_service
from app.services import fhir_autosync, fhir_exchange, fhir_subscription, fhir_sync, trail
from app.services import observations as observations_service
from app.services import organizations as organizations_service
from app.services import patients as patients_service
from app.services.errors import NotFoundError
from app.services.fhir_sync import FhirClient, get_fhir_client

router = APIRouter(prefix="/api/v1/integration/fhir", tags=["integracion FHIR"])

Fhir = Annotated[FhirClient, Depends(get_fhir_client)]


@router.post(
    "/retry-failed",
    summary="Reintentar los pacientes que fallaron o nunca se sincronizaron (solo Admin)",
)
def retry_failed(
    db: DbSession,
    user: CurrentUser,
    client: Fhir,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Pushes again each patient with sync ERROR, PENDING or no sync record.
    One failure does not stop the batch; the answer counts both outcomes."""

    authz.require_admin(user)
    return fhir_sync.retry_unsynced_patients(db, client, actor=user, limit=limit)


@router.post(
    "/subscription",
    summary="Crear o refrescar la Subscription rest-hook en HAPI (solo Admin)",
)
def ensure_subscription(db: DbSession, user: CurrentUser, client: Fhir) -> dict[str, Any]:
    """Idempotent: finds the Subscription by its fixed tag and updates it. The
    response never includes the shared secret."""

    authz.require_admin(user)
    summary = fhir_subscription.ensure_subscription(client)
    trail.audit(
        db,
        action=AuditAction.FHIR_SYNC,
        entity_type="fhir_subscriptions",
        entity_id=None,
        actor=user,
        metadata={"origin": "FHIR", **summary},
    )
    db.commit()
    return summary


@router.post(
    "/import",
    summary="Importar un Bundle FHIR (Patient + Observations). Admin y medico",
)
def import_bundle(
    db: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
    bundle: Annotated[dict[str, Any], Body()],
    eps_organization_id: uuid.UUID | None = Query(
        default=None, description="EPS del paciente nuevo (por defecto, la de la IPS del medico)"
    ),
    organization_id: uuid.UUID | None = Query(
        default=None, description="IPS que recibe la importacion (obligatoria para el admin)"
    ),
) -> dict[str, Any]:
    """Creates or matches the patient by document identifier and imports the
    Observations with origin ``import``. The whole Bundle is validated first:
    any problem is a 422 and nothing is written."""

    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    summary = fhir_exchange.import_bundle(
        db,
        bundle,
        actor=user,
        eps_organization_id=eps_organization_id,
        organization_id=organization_id,
    )
    if summary["paciente_creado"]:
        fhir_autosync.enqueue(background, "patient", uuid.UUID(summary["paciente_id"]), user.id)
    return summary


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
