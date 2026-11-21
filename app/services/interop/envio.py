"""Authorized outbound partner bundles; response/Task processing belongs to R30."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core import authz
from app.core.config import get_settings
from app.fhir.bundle import minimal_partner_bundle, risk_assessment
from app.fhir.diagnostic_report import approved_diagnostic_report
from app.models.enums import AuditAction, RoleCode
from app.models.identity import User
from app.models.patient import Patient
from app.services import ai_reports, clinical_record, consents, patient_access, patients, trail
from app.services.errors import ForbiddenError, InvalidValueError, commit
from app.services.interop import federado, perfil
from app.services.interop.cliente import SendResult, encode
from app.services.rag import privacy

PATIENT_TAG = "[PACIENTE]"


def scrub_for_partner(text: str, patient: Patient | None) -> str:
    """Free text that leaves the system: the patient's identifiers are removed first.

    With the patient bound, name, document, phone, e-mail and address become tags (the
    name becomes ``[PACIENTE]``, never the internal pseudonym). Without it only the generic
    patterns apply (documents, phones, e-mails, street addresses).
    """

    if patient is None:
        return privacy.scrub_generic(text)
    return privacy.scrub(text, privacy.PatientIdentity.of(patient, PATIENT_TAG))


def _scrubbed_item(item: Any, patient: Patient) -> Any:
    """A copy of a clinical-record report that is safe to send: scrubbed notes, no signer."""

    return item.model_copy(update={
        "observaciones": scrub_for_partner(item.observaciones, patient) if item.observaciones else item.observaciones,
        "firmado_por": None,
    })


class BundleSender(Protocol):
    """Implemented by InteropSender; callers inject configured transport or a fake."""

    def enviar_bundle(self, resource: dict) -> SendResult: ...


# Headroom under the receiver's limits: the Bundle must be accepted, not merely close to it.
ENTRY_MARGIN = 10
BYTES_SHARE = 0.75
# Approved reports kept even when the observations alone would fill the Bundle.
REPORT_RESERVE = 20


def _cost(entry: dict[str, Any]) -> int:
    return len(encode(entry)) + 1  # the comma between entries


def _take(candidates: list[dict[str, Any]], room: list[int]) -> list[dict[str, Any]]:
    """The leading candidates that still fit in ``room`` = [entries left, bytes left]."""

    kept: list[dict[str, Any]] = []
    for entry in candidates:
        cost = _cost(entry)
        if room[0] < 1 or room[1] < cost:
            break
        room[0] -= 1
        room[1] -= cost
        kept.append(entry)
    return kept


def _fit_to_limits(
    entries: list[dict[str, Any]], max_entries: int, max_bytes: int, bundle: dict[str, Any]
) -> list[dict[str, Any]]:
    """Keep the Patient and the ServiceRequest, then the most recent observations (they come
    newest first), then the approved reports (newest first), inside the receiver's limits."""

    def kind(entry: dict[str, Any]) -> str:
        return entry["resource"]["resourceType"]

    must = [e for e in entries if kind(e) in ("Patient", "ServiceRequest")]
    observations = [e for e in entries if kind(e) == "Observation"]
    reports = [e for e in entries if kind(e) == "DiagnosticReport"]
    others = [e for e in entries if kind(e) not in ("Patient", "ServiceRequest", "Observation",
                                                      "DiagnosticReport")]
    room = [
        max_entries - ENTRY_MARGIN - len(must),
        int(max_bytes * BYTES_SHARE) - len(encode({**bundle, "entry": []})) - sum(map(_cost, must)),
    ]
    kept = must
    kept += _take(reports[:REPORT_RESERVE], room)
    kept += _take(observations, room)
    kept += _take(reports[REPORT_RESERVE:], room)
    kept += _take(others, room)
    keep_ids = {id(e) for e in kept}
    return [e for e in entries if id(e) in keep_ids]


def send_to_partner(
    db: Session, *, actor: User, patient_id: uuid.UUID, client_id: str, sender: BundleSender,
    service_request: dict | None = None,
    limits: tuple[int, int] | None = None,
    before_send: Callable[[], None] | None = None,
) -> SendResult:
    """Check access and consent before loading clinical content or using transport.

    The caller supplies the sender configured for this destination. Approval is
    read from persisted reports, never accepted as a flag from the request.
    ``service_request`` (R30) is the referral order added to the Bundle.
    ``limits`` (R30) is the receiver's ``(max entries, max body bytes)``: a referral Bundle
    is trimmed to fit with margin. ``before_send`` runs once access, consent and the Bundle
    are settled and right before the transport is used (the caller persists its record there).
    """
    authz.require_role(actor, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    patient = patients.get_patient(db, patient_id)
    patient_access.ensure_staff_patient_scope(db, actor, patient.id)
    try:
        consents.require_valid_consent(
            db, patient_id=patient.id, destination=("interop_client", client_id)
        )
    except ForbiddenError:
        trail.audit(
            db, action=AuditAction.INTEROP_REJECTED, entity_type="patients",
            entity_id=patient.id, actor=actor,
            metadata={"client_id": client_id, "reason": "consent_denied", "direction": "outbound"},
        )
        commit(db)
        raise
    # Fail fast: without a federated id the Bundle would carry no patient identifier and the
    # partner could not match it. Nothing is persisted or sent (the caller's ``before_send``
    # has not run), and the refusal is audited.
    federated_id = patient.id_federado or federado.for_patient(patient)
    if not federated_id:
        trail.audit(
            db, action=AuditAction.INTEROP_REJECTED, entity_type="patients",
            entity_id=patient.id, actor=actor,
            metadata={"client_id": client_id, "reason": "no_federated_id", "direction": "outbound"},
        )
        commit(db)
        raise InvalidValueError(
            "El paciente no tiene identificador federado (FEDERATED_SECRET sin configurar o tipo de "
            "documento sin identificador federado): no se envia nada al socio"
        )
    observations = clinical_record._observations(db, patient.id, actor)
    approved = ai_reports.approved_reports(db, patient.id)
    if service_request is None:
        # The clinician's free-text observations leave with the patient's identifiers removed
        # (name, document, phone, e-mail, address), and the signer's name is not sent at all.
        reports = [
            risk_assessment(
                _scrubbed_item(clinical_record._report_item(report), patient),
                identifier_system=get_settings().fhir_identifier_system, patient_ref="",
            )
            for report in approved
        ]
    else:
        # R30: a referral carries each approved analysis ONCE, as a final DiagnosticReport
        # (the receiver drops RiskAssessment, so sending both only costs size).
        reports = [approved_diagnostic_report(report) for report in approved]
    settings = get_settings()
    bundle = minimal_partner_bundle(
        patient, observations, reports, service_request=service_request,
        federated_id=None if patient.id_federado else federated_id,
        # Negotiated minimization trade-off (Ley 1581): the document only travels when the
        # partner cannot compute the federated id, and the federated id is always sent too.
        document_identifier=(
            federado.document_identifier_for(patient, system=settings.interop_document_system)
            if settings.interop_send_document_identifier else None
        ),
    )
    if service_request is not None:
        # A referral must not fail at the partner because of one local row that
        # does not meet the common profile (unit, code system, no numeric value).
        bundle["entry"] = [
            entry for entry in bundle["entry"]
            if entry["resource"]["resourceType"] != "Observation"
            or not perfil.validate_observation(entry["resource"])
        ]
        if limits is not None:
            bundle["entry"] = _fit_to_limits(bundle["entry"], *limits, bundle)
    if before_send is not None:
        before_send()
    result = sender.enviar_bundle(bundle)
    trail.audit(
        db, action=AuditAction.INTEROP_SENT, entity_type="patients", entity_id=patient.id,
        actor=actor, metadata={"client_id": client_id, "recurso": "Bundle",
                               "codigo_http": result.status, "entregado": result.entregado},
    )
    commit(db)
    return result
