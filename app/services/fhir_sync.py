"""Synchronization of relational rows to the HAPI FHIR server.

Idempotency is the design center of this module, guaranteed by two layers:

1. **Conditional update.** Resources are sent with
   ``PUT [base]/{Type}?identifier={system}|{value}``, which FHIR resolves as
   "create if absent, update if present". Running the synchronization twice
   therefore never duplicates a resource -- and it makes PUT support inherent
   rather than an extra.
2. **One log row per entity.** ``fhir_sync_log`` is UNIQUE on
   ``(entity_type, entity_id)``; the id assigned by the server is recorded
   there, and later syncs of dependent resources reference it.

Dependencies are synchronized before dependents (an Encounter needs its
Patient's server id to reference it), so a single call transparently pushes
whatever chain the resource requires.

Failures are recorded, not swallowed: an unreachable server or a rejected
payload lands in the log with ``sync_status = ERROR`` and the message, then
surfaces to the API as a 502.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.fhir import mappers
from app.models.base import Base
from app.models.clinical import Encounter, Observation
from app.models.enums import AuditAction, SyncStatus
from app.models.governance import FhirSyncLog
from app.models.identity import User
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.services import trail
from app.services.errors import FhirGatewayError, NotFoundError, commit

settings = get_settings()


class FhirClient:
    """Thin wrapper over httpx for talking FHIR JSON to HAPI."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def conditional_update(
        self, resource_type: str, identifier_system: str, identifier_value: str,
        payload: dict[str, Any],
    ) -> tuple[str, str | None]:
        """PUT by identifier; return the server-assigned (id, versionId)."""

        try:
            response = self._http.put(
                f"/{resource_type}",
                params={"identifier": f"{identifier_system}|{identifier_value}"},
                json=payload,
                headers={"Content-Type": "application/fhir+json"},
            )
        except httpx.HTTPError as exc:
            raise FhirGatewayError(
                f"No se pudo contactar el servidor FHIR: {exc}"
            ) from exc

        if response.status_code not in (200, 201):
            raise FhirGatewayError(
                f"El servidor FHIR rechazo el recurso {resource_type} "
                f"({response.status_code}): {response.text[:500]}"
            )

        body = response.json()
        return body["id"], body.get("meta", {}).get("versionId")

    def read(self, resource_type: str, fhir_id: str) -> dict[str, Any]:
        try:
            response = self._http.get(f"/{resource_type}/{fhir_id}")
        except httpx.HTTPError as exc:
            raise FhirGatewayError(
                f"No se pudo contactar el servidor FHIR: {exc}"
            ) from exc
        if response.status_code == 404:
            raise NotFoundError(
                f"El recurso {resource_type}/{fhir_id} no existe en el servidor FHIR"
            )
        if response.status_code != 200:
            raise FhirGatewayError(
                f"Error del servidor FHIR ({response.status_code}): "
                f"{response.text[:500]}"
            )
        return response.json()


def get_fhir_client() -> Iterator[FhirClient]:
    """FastAPI dependency: one HTTP client per request, always closed."""

    with httpx.Client(
        base_url=settings.fhir_base_url,
        timeout=settings.fhir_request_timeout_seconds,
    ) as http:
        yield FhirClient(http)


# --------------------------------------------------------------------------
# Sync log bookkeeping
# --------------------------------------------------------------------------


def get_sync_record(
    db: Session, entity_type: str, entity_id: uuid.UUID
) -> FhirSyncLog | None:
    return db.scalar(
        select(FhirSyncLog).where(
            FhirSyncLog.entity_type == entity_type,
            FhirSyncLog.entity_id == entity_id,
        )
    )


def _record_result(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    resource_type: str,
    payload: dict[str, Any] | None,
    fhir_id: str | None,
    version_id: str | None,
    error: str | None,
) -> FhirSyncLog:
    record = get_sync_record(db, entity_type, entity_id)
    if record is None:
        record = FhirSyncLog(
            entity_type=entity_type,
            entity_id=entity_id,
            fhir_resource_type=resource_type,
        )
        db.add(record)

    record.attempt_count += 1
    record.last_payload = payload
    if error is None:
        record.fhir_resource_id = fhir_id
        record.fhir_version_id = version_id
        record.sync_status = SyncStatus.SYNCED
        record.last_synced_at = datetime.now(timezone.utc)
        record.error_message = None
    else:
        record.sync_status = SyncStatus.ERROR
        # The message is capped by the column, not by hope.
        record.error_message = error[:1000]
    return record


def _push(
    db: Session,
    client: FhirClient,
    *,
    entity: Base,
    actor: User,
    resource_type: str,
    identifier_system: str,
    identifier_value: str,
    payload: dict[str, Any],
) -> FhirSyncLog:
    """Send one resource and persist the outcome, success or failure."""

    entity_type = entity.__tablename__
    entity_id = entity.id  # type: ignore[attr-defined]
    try:
        fhir_id, version_id = client.conditional_update(
            resource_type, identifier_system, identifier_value, payload
        )
    except (FhirGatewayError, NotFoundError) as exc:
        _record_result(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            resource_type=resource_type,
            payload=payload,
            fhir_id=None,
            version_id=None,
            error=str(exc),
        )
        commit(db)
        raise

    record = _record_result(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        resource_type=resource_type,
        payload=payload,
        fhir_id=fhir_id,
        version_id=version_id,
        error=None,
    )
    trail.audit(
        db,
        action=AuditAction.FHIR_SYNC,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        metadata={"fhir_resource": f"{resource_type}/{fhir_id}"},
    )
    commit(db)
    db.refresh(record)
    return record


# --------------------------------------------------------------------------
# Per-entity synchronization, dependencies first
# --------------------------------------------------------------------------


def sync_organization(
    db: Session, client: FhirClient, organization: Organization, *, actor: User
) -> FhirSyncLog:
    part_of_ref: str | None = None
    if organization.parent_organization_id is not None:
        parent = db.get(Organization, organization.parent_organization_id)
        if parent is not None:
            parent_record = sync_organization(db, client, parent, actor=actor)
            part_of_ref = f"Organization/{parent_record.fhir_resource_id}"

    payload = mappers.organization_to_fhir(
        organization,
        identifier_system=settings.fhir_identifier_system,
        part_of_ref=part_of_ref,
    )
    return _push(
        db,
        client,
        entity=organization,
        actor=actor,
        resource_type="Organization",
        identifier_system=f"{settings.fhir_identifier_system}:organizations",
        identifier_value=organization.code,
        payload=payload,
    )


def sync_location(
    db: Session, client: FhirClient, location: Location, *, actor: User
) -> FhirSyncLog:
    organization = db.get(Organization, location.organization_id)
    if organization is None:
        raise NotFoundError("La ubicacion no tiene organizacion valida")
    org_record = sync_organization(db, client, organization, actor=actor)

    part_of_ref: str | None = None
    if location.parent_location_id is not None:
        parent = db.get(Location, location.parent_location_id)
        if parent is not None:
            parent_record = sync_location(db, client, parent, actor=actor)
            part_of_ref = f"Location/{parent_record.fhir_resource_id}"

    payload = mappers.location_to_fhir(
        location,
        identifier_system=settings.fhir_identifier_system,
        managing_organization_ref=f"Organization/{org_record.fhir_resource_id}",
        part_of_ref=part_of_ref,
    )
    return _push(
        db,
        client,
        entity=location,
        actor=actor,
        resource_type="Location",
        identifier_system=f"{settings.fhir_identifier_system}:locations",
        identifier_value=location.code,
        payload=payload,
    )


def sync_patient(
    db: Session, client: FhirClient, patient: Patient, *, actor: User
) -> FhirSyncLog:
    managing_ref: str | None = None
    eps = db.get(Organization, patient.eps_organization_id)
    if eps is not None:
        eps_record = sync_organization(db, client, eps, actor=actor)
        managing_ref = f"Organization/{eps_record.fhir_resource_id}"

    payload = mappers.patient_to_fhir(
        patient,
        identifier_system=settings.fhir_identifier_system,
        managing_organization_ref=managing_ref,
    )
    return _push(
        db,
        client,
        entity=patient,
        actor=actor,
        resource_type="Patient",
        identifier_system=f"{settings.fhir_identifier_system}:documento",
        identifier_value=patient.business_identifier,
        payload=payload,
    )


def sync_encounter(
    db: Session, client: FhirClient, encounter: Encounter, *, actor: User
) -> FhirSyncLog:
    patient = db.get(Patient, encounter.patient_id)
    if patient is None:
        raise NotFoundError("El encuentro no tiene paciente valido")
    patient_record = sync_patient(db, client, patient, actor=actor)

    provider_ref: str | None = None
    organization = db.get(Organization, encounter.organization_id)
    if organization is not None:
        org_record = sync_organization(db, client, organization, actor=actor)
        provider_ref = f"Organization/{org_record.fhir_resource_id}"

    location_ref: str | None = None
    if encounter.location_id is not None:
        location = db.get(Location, encounter.location_id)
        if location is not None:
            loc_record = sync_location(db, client, location, actor=actor)
            location_ref = f"Location/{loc_record.fhir_resource_id}"

    payload = mappers.encounter_to_fhir(
        encounter,
        identifier_system=settings.fhir_identifier_system,
        patient_ref=f"Patient/{patient_record.fhir_resource_id}",
        service_provider_ref=provider_ref,
        location_ref=location_ref,
    )
    return _push(
        db,
        client,
        entity=encounter,
        actor=actor,
        resource_type="Encounter",
        identifier_system=f"{settings.fhir_identifier_system}:encounters",
        identifier_value=str(encounter.id),
        payload=payload,
    )


def sync_observation(
    db: Session, client: FhirClient, observation: Observation, *, actor: User
) -> FhirSyncLog:
    patient = db.get(Patient, observation.patient_id)
    encounter = db.get(Encounter, observation.encounter_id)
    if patient is None or encounter is None:
        raise NotFoundError("La observacion no tiene paciente o encuentro valido")
    patient_record = sync_patient(db, client, patient, actor=actor)
    encounter_record = sync_encounter(db, client, encounter, actor=actor)

    payload = mappers.observation_to_fhir(
        observation,
        identifier_system=settings.fhir_identifier_system,
        patient_ref=f"Patient/{patient_record.fhir_resource_id}",
        encounter_ref=f"Encounter/{encounter_record.fhir_resource_id}",
    )
    return _push(
        db,
        client,
        entity=observation,
        actor=actor,
        resource_type="Observation",
        identifier_system=f"{settings.fhir_identifier_system}:observations",
        identifier_value=str(observation.id),
        payload=payload,
    )


def read_synced_resource(
    db: Session, client: FhirClient, entity_type: str, entity_id: uuid.UUID
) -> dict[str, Any]:
    """Fetch from HAPI the resource that mirrors one local row.

    The read goes through the sync log on purpose: it proves the round trip
    (our id -> server id -> server content) instead of just echoing our data.
    """

    record = get_sync_record(db, entity_type, entity_id)
    if record is None or record.fhir_resource_id is None:
        raise NotFoundError(
            "El registro aun no ha sido sincronizado con el servidor FHIR"
        )
    return client.read(record.fhir_resource_type, record.fhir_resource_id)
