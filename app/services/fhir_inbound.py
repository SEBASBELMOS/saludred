"""Observations delivered by the FHIR server (R21).

HAPI calls the rest-hook endpoint when an Observation is stored. This module
decides what the system does with it:

1. **Echo.** Observations that SaludRed itself pushed (R15) also trigger the
   Subscription. They carry the origin tag set by the mappers and are ignored
   silently; otherwise every local observation would notify its doctor twice.
2. **Idempotency.** HAPI retries deliveries it thinks failed. The pair
   ``(Observation id, versionId)`` is recorded once in ``fhir_inbound_events``
   (UNIQUE); a second arrival changes nothing.
3. **Patient.** Resolved from ``subject.reference`` (``Patient/{fhir id}``)
   through ``fhir_sync_log``, or from a ``subject.identifier`` that uses the
   project's identifier systems. An unknown patient is audited and nothing is
   created.
4. **Storage.** The result is stored in ``observations`` with origin ``fhir``,
   attached to the patient's open encounter or, failing that, the most recent
   one. ``observations.encounter_id`` is NOT NULL and the institutional scope of
   an observation is inherited from its encounter, so a patient with no
   encounter at all gets no stored row: the delivery is still audited, the
   resource is kept in ``fhir_inbound_events`` and the doctor is still told.
5. **Reaction.** Audit (FHIR_EVENT_RECEIVED, origin FHIR) and a domain event
   ``fhir_observacion`` for the responsible clinician (or the fallback).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import events
from app.core.config import get_settings
from app.fhir import mappers
from app.fhir.parsing import (
    FhirParseError,
    ParsedObservation,
    SubjectRef,
    parse_observation,
    parse_subject,
)
from app.models.clinical import Encounter, Observation
from app.models.enums import (
    AuditAction,
    DocumentType,
    EncounterStatus,
    FhirInboundOutcome,
    ObservationOrigin,
)
from app.models.governance import FhirInboundEvent, FhirSyncLog
from app.models.patient import Patient
from app.services import recipients, trail
from app.services.errors import InvalidValueError

logger = logging.getLogger(__name__)

EVENT_TYPE = "fhir_observacion"
OPEN_ENCOUNTER_STATUSES = (EncounterStatus.IN_PROGRESS, EncounterStatus.ARRIVED)


@dataclass
class InboundResult:
    outcome: str  # processed | unknown_patient | duplicate | echo
    patient_id: uuid.UUID | None = None
    observation_id: uuid.UUID | None = None
    stored: bool = False
    recipients: tuple[uuid.UUID, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Statements (kept separate so they can be compiled for PostgreSQL in tests)
# ---------------------------------------------------------------------------


def patient_by_fhir_id_stmt(fhir_id: str) -> Select[tuple[Patient]]:
    return (
        select(Patient)
        .join(
            FhirSyncLog,
            and_(
                FhirSyncLog.entity_type == Patient.__tablename__,
                FhirSyncLog.entity_id == Patient.id,
            ),
        )
        .where(
            FhirSyncLog.fhir_resource_type == "Patient",
            FhirSyncLog.fhir_resource_id == fhir_id,
            Patient.deleted_at.is_(None),
        )
    )


def inbound_event_stmt(
    resource_type: str, resource_id: str, version_id: str
) -> Select[tuple[uuid.UUID]]:
    return select(FhirInboundEvent.id).where(
        FhirInboundEvent.fhir_resource_type == resource_type,
        FhirInboundEvent.fhir_resource_id == resource_id,
        FhirInboundEvent.fhir_version_id == version_id,
    )


def target_encounter_stmt(patient_id: uuid.UUID, *, open_only: bool) -> Select[tuple[Encounter]]:
    stmt = select(Encounter).where(
        Encounter.patient_id == patient_id, Encounter.deleted_at.is_(None)
    )
    if open_only:
        stmt = stmt.where(
            Encounter.ended_at.is_(None), Encounter.status.in_(OPEN_ENCOUNTER_STATUSES)
        )
    return stmt.order_by(Encounter.started_at.desc(), Encounter.id).limit(1)


# ---------------------------------------------------------------------------
# Patient resolution
# ---------------------------------------------------------------------------


def resolve_patient(db: Session, subject: SubjectRef | None) -> Patient | None:
    if subject is None:
        return None
    if subject.fhir_id:
        return db.scalars(patient_by_fhir_id_stmt(subject.fhir_id)).unique().first()
    if subject.identifier_value:
        return patient_by_identifier(db, subject.identifier_system, subject.identifier_value)
    return None


def patient_by_identifier(
    db: Session, system: str | None, value: str
) -> Patient | None:
    """Find a patient by one of the project's identifier systems.

    Without a ``system`` (what a plain client sends) the value is tried as the
    API patient UUID, then as a bare document number, then as ``<type>-<number>``;
    the first form that matches decides, and a form matching several patients is
    ambiguous, so the patient stays unknown rather than guessed.
    """

    prefix = get_settings().fhir_identifier_system
    base = select(Patient).where(Patient.deleted_at.is_(None))
    if not system:
        return _patient_by_bare_value(db, base, value.strip())
    if system == f"{prefix}:documento":
        doc_type, _, number = value.partition("-")
        try:
            document_type = DocumentType(doc_type)
        except ValueError:
            return None
        stmt = base.where(
            Patient.document_type == document_type, Patient.document_number == number
        )
    elif system == f"{prefix}:numero":
        stmt = base.where(Patient.document_number == value)
    elif system == f"{prefix}:patient-id":
        try:
            stmt = base.where(Patient.id == uuid.UUID(value))
        except ValueError:
            return None
    else:
        return None
    matches = list(db.scalars(stmt.limit(2)).unique())
    # A bare number can repeat across document types: ambiguous is unknown.
    return matches[0] if len(matches) == 1 else None


def _patient_by_bare_value(db: Session, base: Select[tuple[Patient]], value: str) -> Patient | None:
    attempts: list[Select[tuple[Patient]]] = []
    try:
        attempts.append(base.where(Patient.id == uuid.UUID(value)))
    except ValueError:
        pass
    attempts.append(base.where(Patient.document_number == value))
    doc_type, _, number = value.partition("-")
    if number:
        try:
            attempts.append(
                base.where(
                    Patient.document_type == DocumentType(doc_type),
                    Patient.document_number == number,
                )
            )
        except ValueError:
            pass
    for stmt in attempts:
        matches = list(db.scalars(stmt.limit(2)).unique())
        if matches:
            return matches[0] if len(matches) == 1 else None
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _version_of(resource: dict[str, Any]) -> str:
    version = (resource.get("meta") or {}).get("versionId")
    if version:
        return str(version)[:80]
    digest = hashlib.sha256(
        json.dumps(resource, sort_keys=True, default=str).encode()
    ).hexdigest()
    return f"sha256:{digest[:32]}"


def is_echo(resource: dict[str, Any]) -> bool:
    return mappers.is_own_resource(resource, get_settings().fhir_identifier_system)


def _event_data(
    parsed: ParsedObservation,
    *,
    fhir_id: str,
    version_id: str,
    patient: Patient,
    observation: Observation | None,
) -> dict[str, Any]:
    return {
        "origen": "FHIR",
        "fhir_resource": f"Observation/{fhir_id}",
        "fhir_version_id": version_id,
        "observacion_id": str(observation.id) if observation else None,
        "almacenada": observation is not None,
        "codigo": parsed.code,
        "nombre": parsed.label,
        "valor": parsed.value_label or None,
        "paciente_nombre": patient.full_name,
    }


def handle_observation(
    db: Session, resource: dict[str, Any], *, path_id: str | None = None
) -> InboundResult:
    """Process one delivered Observation. Raises InvalidValueError (422) if unusable."""

    if resource.get("resourceType") != "Observation":
        raise InvalidValueError("El recurso recibido no es una Observation")

    if is_echo(resource):
        logger.debug("Ignoring echo of an Observation pushed by SaludRed")
        return InboundResult(outcome="echo")

    fhir_id = str(resource.get("id") or path_id or "").strip()
    if not fhir_id:
        raise InvalidValueError("La Observation recibida no trae id")
    version_id = _version_of(resource)

    try:
        parsed = parse_observation(resource)
    except FhirParseError as exc:
        raise InvalidValueError(str(exc)) from exc

    if db.scalar(inbound_event_stmt("Observation", fhir_id, version_id)) is not None:
        return InboundResult(outcome="duplicate")

    subject = parse_subject(resource)
    patient = resolve_patient(db, subject)

    if patient is None:
        record = FhirInboundEvent(
            fhir_resource_type="Observation",
            fhir_resource_id=fhir_id,
            fhir_version_id=version_id,
            outcome=FhirInboundOutcome.UNKNOWN_PATIENT,
            resource=resource,
        )
        db.add(record)
        if _flush_is_duplicate(db):
            return InboundResult(outcome="duplicate")
        trail.audit(
            db,
            action=AuditAction.FHIR_EVENT_RECEIVED,
            entity_type="fhir_inbound_events",
            entity_id=record.id,
            actor=None,
            metadata={
                "origin": "FHIR",
                "outcome": FhirInboundOutcome.UNKNOWN_PATIENT.value,
                "fhir_resource": f"Observation/{fhir_id}",
                "fhir_version_id": version_id,
                "subject": (resource.get("subject") or {}),
            },
        )
        db.commit()
        return InboundResult(outcome="unknown_patient")

    observation: Observation | None = None
    encounter = _target_encounter(db, patient.id)
    record = FhirInboundEvent(
        fhir_resource_type="Observation",
        fhir_resource_id=fhir_id,
        fhir_version_id=version_id,
        outcome=FhirInboundOutcome.PROCESSED,
        patient_id=patient.id,
        resource=resource,
    )
    db.add(record)
    if _flush_is_duplicate(db):
        return InboundResult(outcome="duplicate")

    if encounter is not None and parsed.has_value:
        observation = Observation(
            patient_id=patient.id,
            encounter_id=encounter.id,
            status=parsed.status,
            code_system=parsed.code_system,
            code=parsed.code,
            display=parsed.display,
            value_numeric=parsed.value_numeric,
            value_text=parsed.value_text,
            unit=parsed.unit,
            observed_at=parsed.observed_at,
            origin=ObservationOrigin.FHIR,
            fhir_resource_id=fhir_id,
            created_by=None,
        )
        db.add(observation)
        db.flush()
        record.observation_id = observation.id

    addressed = recipients.for_patient(db, patient)
    trail.audit(
        db,
        action=AuditAction.FHIR_EVENT_RECEIVED,
        entity_type=patient.__tablename__,
        entity_id=patient.id,
        actor=None,
        metadata={
            "origin": "FHIR",
            "outcome": FhirInboundOutcome.PROCESSED.value,
            "patient_id": str(patient.id),
            "fhir_resource": f"Observation/{fhir_id}",
            "fhir_version_id": version_id,
            "observation_id": str(observation.id) if observation else None,
            "stored": observation is not None,
            "code": parsed.code,
            "inbound_event_id": str(record.id),
            "recipients": [str(r) for r in addressed],
        },
    )
    db.commit()

    events.publish(
        events.DomainEvent(
            tipo=EVENT_TYPE,
            mensaje=f"Nuevo resultado de laboratorio desde FHIR: {parsed.describe()}",
            recipients=addressed,
            paciente_id=patient.id,
            datos=_event_data(
                parsed, fhir_id=fhir_id, version_id=version_id,
                patient=patient, observation=observation,
            ),
        ),
        db,
    )
    return InboundResult(
        outcome="processed",
        patient_id=patient.id,
        observation_id=observation.id if observation else None,
        stored=observation is not None,
        recipients=addressed,
    )


def _target_encounter(db: Session, patient_id: uuid.UUID) -> Encounter | None:
    for open_only in (True, False):
        encounter = db.scalar(target_encounter_stmt(patient_id, open_only=open_only))
        if encounter is not None:
            return encounter
    return None


def _flush_is_duplicate(db: Session) -> bool:
    """Flush; a UNIQUE violation means a concurrent delivery won the race."""

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return True
    return False
