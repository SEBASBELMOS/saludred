"""R30: referral round trip between two institutions.

The same code plays both roles. A deployment is **A** when it sends a referral and **B**
when it receives one.

A -> B   ``refer_to_partner`` builds the Bundle through ``envio.send_to_partner`` (scope,
         consent, minimal Bundle) and posts it to ``POST /interop/Bundle``. B validates the
         WHOLE Bundle (``validate_bundle``) before it writes anything, then creates the
         referral with external origin in one transaction (``finish_bundle``).
B -> A   When B's specialist accepts or rejects that referral through the existing flow,
         ``stage_answer`` writes the ``Task`` to ``interop_task_outbox`` INSIDE the same
         transaction as the status change (both stored or neither), and a background task
         (``deliver_queued``, own session) sends it to ``POST /interop/Task``. A network
         failure never rolls back the decision and ``retry_outbox`` can send it again. The
         reason of a rejection is scrubbed of the patient's identifiers before it travels.
A        ``_receive_task`` finds the outbound referral by the ``ServiceRequest`` identifier,
         checks that it belongs to the calling partner, updates its status and tells the
         sending doctor live (``respuesta_socio``). A replay is idempotent. A partner may also
         answer with an update of the ``ServiceRequest`` (``active`` accepted, ``revoked``
         rejected) on ``POST /interop/ServiceRequest``: ``receive_reply``, same effect.

Transport, signature, token and size limits are those of ``receptor``: this module only
adds the Bundle and Task contracts.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import events
from app.core.config import get_settings
from app.fhir.parsing import FhirParseError, parse_observation
from app.models.clinical import Observation
from app.models.enums import (
    AuditAction,
    FhirInboundOutcome,
    ObservationOrigin,
    ReferralStatus,
    RoleCode,
)
from app.models.identity import User
from app.models.interop import InteropClient
from app.models.interop_referral import InteropTaskOutbox, OutboundReferral, PartnerDiagnosticReport
from app.models.patient import Patient
from app.models.referral import Referral
from app.services import fhir_inbound, trail
from app.services import referrals as referrals_service
from app.services.errors import (
    ConflictError,
    FhirGatewayError,
    InvalidValueError,
    NotFoundError,
    commit,
)
from app.services.interop import destinations, envio, federado, perfil, receptor
from app.services.interop.cliente import SenderError, SendResult
from app.services.interop.federado import looks_like_federated_id
from app.services.interop.outcome import FHIR_JSON, Issue, informational, operation_outcome

logger = logging.getLogger(__name__)

REQUEST_SYSTEM = "urn:saludred:referral-request"
EVENT_RESPONSE = "respuesta_socio"
# Marks referrals created from a Bundle: only those are answered with a Task.
BUNDLE_KEY_PREFIX = "interop-bundle:"

STATUS_SENDING = "enviando"  # persisted BEFORE the transport is used
STATUS_SENT = "enviada"
STATUS_SEND_ERROR = "error_envio"  # transport failed; a late Task can still arrive and match
STATUS_ACCEPTED = "aceptada"
STATUS_REJECTED = "rechazada"
STATUS_INVALID = "rechazada_por_validacion"
OUTBOUND_STATUSES = (STATUS_SENDING, STATUS_SENT, STATUS_SEND_ERROR, STATUS_ACCEPTED, STATUS_REJECTED,
                     STATUS_INVALID)
# A Task may answer a referral in any of these: B may have committed the Bundle even when
# A never saw the answer (timeout), so the Task can overtake A's own bookkeeping.
AWAITING_ANSWER = (STATUS_SENDING, STATUS_SENT, STATUS_SEND_ERROR)

MAX_DELIVERY_ATTEMPTS = 8
PERMANENT_PREFIX = "permanente: "
RETRYABLE_CLIENT_ERRORS = (408, 429)
CREDENTIAL_ERRORS = (401, 403)  # the partner can fix its key or scope: retried up to the cap

PRIORITIES = {"rutina": "routine", "urgente": "urgent", "inmediata": "asap"}
TASK_STATUSES = {"accepted": STATUS_ACCEPTED, "rejected": STATUS_REJECTED}

BUNDLE_RESOURCES = frozenset(
    {"Patient", "ServiceRequest", "Observation", "DiagnosticReport", "RiskAssessment"}
)
DIRECT_IDENTIFIERS = ("name", "telecom", "address", "birthDate")
MAX_ENTRIES = 200
MAX_SUMMARY = 2000
MAX_REFERENCE = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clip(text: str | None, limit: int = MAX_SUMMARY) -> str:
    return (text or "").strip()[:limit]


# ---------------------------------------------------------------------------
# Contract: ServiceRequest and Task builders / readers
# ---------------------------------------------------------------------------


def build_referral_request(request_id: str, reason: str, service: str, priority: str) -> dict[str, Any]:
    """The referral order of the Bundle. The subject is added by ``minimal_partner_bundle``."""

    return {
        "resourceType": "ServiceRequest",
        "id": request_id,
        "identifier": [{"system": REQUEST_SYSTEM, "value": request_id}],
        "status": "active",
        "intent": "order",
        "priority": PRIORITIES[priority],
        "code": {"coding": [{"system": perfil.SNOMED, "code": perfil.REFERRAL_CODE,
                              "display": "Patient referral"}]},
        "orderDetail": [{"text": service}],
        "authoredOn": _now().isoformat(timespec="seconds"),
        "reasonCode": [{"text": reason}],
    }


def request_identifier(resource: dict[str, Any]) -> str | None:
    """The correlation id of a ServiceRequest: its identifier, else its ``id``."""

    identifiers = resource.get("identifier")
    for item in identifiers if isinstance(identifiers, list) else []:
        value = str(_dict(item).get("value") or "").strip()
        if value and _dict(item).get("system") == REQUEST_SYSTEM:
            return value[:MAX_REFERENCE]
    value = str(resource.get("id") or "").strip()
    return value[:MAX_REFERENCE] or None


def _service_of(request: dict[str, Any]) -> str:
    details = request.get("orderDetail")
    for item in details if isinstance(details, list) else []:
        text = str(_dict(item).get("text") or "").strip()
        if text:
            return text[:200]
    return ""


# Defined in ``envio`` (which this module imports) so the outbound Bundle can use it too.
PATIENT_TAG = envio.PATIENT_TAG
scrub_for_partner = envio.scrub_for_partner


def build_task(referral: Referral, patient: Patient | None = None) -> dict[str, Any]:
    """The answer of B: ``accepted`` or ``rejected`` for the ServiceRequest of A.

    The rejection reason is free text typed by the specialist: it is scrubbed with the
    patient bound before it travels (the partner needs the reason, not who the patient is).
    """

    rejected = referral.status == ReferralStatus.REJECTED
    task: dict[str, Any] = {
        "resourceType": "Task",
        "status": "rejected" if rejected else "accepted",
        "intent": "order",
        "focus": {"identifier": {"system": REQUEST_SYSTEM, "value": referral.referencia_externa}},
        "lastModified": (referral.rejected_at if rejected else referral.accepted_at or _now()).isoformat(),
    }
    if rejected:
        task["statusReason"] = {"text": scrub_for_partner(referral.motivo_rechazo or "", patient)}
    return task


def validate_task(task: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    if task.get("resourceType") != "Task":
        return [Issue("structure", "El recurso debe ser una Task", ("resourceType",))]
    if task.get("status") not in TASK_STATUSES:
        issues.append(Issue("value", "Task.status debe ser accepted o rejected", ("Task.status",)))
    identifier = _dict(_dict(task.get("focus")).get("identifier"))
    value = str(identifier.get("value") or "").strip()
    if not value or len(value) > MAX_REFERENCE:
        issues.append(Issue("required", "Task.focus.identifier.value (identificador de la "
                            "ServiceRequest original) es obligatorio", ("Task.focus.identifier.value",)))
    if task.get("status") == "rejected" and not str(_dict(task.get("statusReason")).get("text") or "").strip():
        issues.append(Issue("required", "Task.statusReason.text es obligatorio al rechazar",
                            ("Task.statusReason.text",)))
    return issues


# ---------------------------------------------------------------------------
# B: validate the WHOLE Bundle before writing anything
# ---------------------------------------------------------------------------


def _patient_issues(patient: dict[str, Any], where: str) -> tuple[str | None, list[Issue]]:
    issues: list[Issue] = []
    for key in DIRECT_IDENTIFIERS:
        if key in patient:
            issues.append(Issue("business-rule", f"El Patient minimo no debe incluir {key}",
                                (f"{where}.{key}",)))
    expected = get_settings().federated_system
    identifiers = patient.get("identifier")
    items = [_dict(item) for item in identifiers] if isinstance(identifiers, list) else []
    for item in items:
        if item.get("system") == expected and looks_like_federated_id(item.get("value")):
            return str(item["value"]), issues
    # Any-partner mode: a national document (type and number) instead of the federated id.
    # Direct identifiers such as telecom and address stay rejected above.
    for item in items:
        if perfil.is_document_identifier(item):
            if not federado.enabled():
                return "", issues  # the receiver answers 503 right after the profile passes
            computed = federado.federated_id_of_document(item)
            if computed is None:
                issues.append(Issue("value", "El documento del Patient no es valido "
                                    f"(tipo {', '.join(federado.TYPES)} y numero)", (f"{where}.identifier",)))
            return computed, issues
    issues.append(Issue("required", f"Patient.identifier con el sistema {expected} y el identificador "
                        "federado es obligatorio", (f"{where}.identifier",)))
    return None, issues


def validate_bundle(bundle: Any, client_id: str) -> list[dict[str, Any]]:
    """The resources of a conforming referral Bundle; ``Rejection`` (422) otherwise."""

    def refuse(issues: list[Issue]) -> receptor.Rejection:
        return receptor.Rejection(422, issues, "perfil", client_id=client_id)

    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise refuse([Issue("structure", "El recurso debe ser un Bundle", ("resourceType",))])
    issues: list[Issue] = []
    if bundle.get("type") != "collection":
        issues.append(Issue("value", "Bundle.type debe ser collection", ("Bundle.type",)))
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        raise refuse(issues + [Issue("required", "Bundle.entry no puede estar vacio", ("Bundle.entry",))])
    if len(entries) > MAX_ENTRIES:
        raise refuse(issues + [Issue("too-costly", f"El Bundle supera {MAX_ENTRIES} entradas",
                                     ("Bundle.entry",))])

    indexed: list[tuple[int, dict[str, Any]]] = []
    for i, entry in enumerate(entries):
        resource = _dict(entry).get("resource")
        kind = resource.get("resourceType") if isinstance(resource, dict) else None
        if kind not in BUNDLE_RESOURCES:
            issues.append(Issue("structure", f"Entrada {i}: recurso no admitido ({kind!r}); "
                                f"se admiten {', '.join(sorted(BUNDLE_RESOURCES))}",
                                (f"Bundle.entry[{i}].resource",)))
            continue
        indexed.append((i, resource))

    patients = [(i, r) for i, r in indexed if r["resourceType"] == "Patient"]
    requests = [(i, r) for i, r in indexed if r["resourceType"] == "ServiceRequest"]
    if len(patients) != 1:
        issues.append(Issue("structure", "El Bundle debe tener exactamente un Patient", ("Bundle.entry",)))
    if len(requests) != 1:
        issues.append(Issue("structure", "El Bundle debe tener exactamente una ServiceRequest",
                            ("Bundle.entry",)))

    federated_id: str | None = None
    if len(patients) == 1:
        i, patient = patients[0]
        federated_id, found = _patient_issues(patient, f"Bundle.entry[{i}].resource")
        issues += found

    for i, resource in indexed:
        kind = resource["resourceType"]
        where = f"Bundle.entry[{i}].resource"
        if kind == "ServiceRequest":
            issues += perfil.validate_service_request(resource)
            if request_identifier(resource) is None:
                issues.append(Issue("required", "ServiceRequest.identifier es obligatorio para correlacionar "
                                    "la respuesta", (f"{where}.identifier",)))
        elif kind == "Observation":
            found = perfil.validate_observation(resource)
            issues += found
            if not found:
                try:
                    parse_observation(resource)
                except FhirParseError as exc:
                    issues.append(Issue("value", str(exc), (where,)))
        elif kind == "DiagnosticReport" and resource.get("status") != "final":
            issues.append(Issue("business-rule", "Solo se aceptan DiagnosticReport con status final",
                                (f"{where}.status",)))
        if kind != "Patient" and federated_id is not None and perfil.subject_federated_id(resource) != federated_id:
            issues.append(Issue("invalid", f"{kind}.subject no coincide con el Patient del Bundle",
                                (f"{where}.subject",)))
    if issues:
        raise refuse(issues)
    return [resource for _, resource in indexed]


# ---------------------------------------------------------------------------
# B: store the referral (one transaction)
# ---------------------------------------------------------------------------


def finish_bundle(
    db: Session,
    record: Any,
    resources: list[dict[str, Any]],
    patient: Patient,
    specialist: User,
    ip: str | None,
    reconciled_by: User | None = None,
) -> Referral:
    """Referral + evidence + audit in ONE commit; then the live announcement."""

    label = record.origen_externo or "externo"
    request = next(r for r in resources if r["resourceType"] == "ServiceRequest")
    correlation = request_identifier(request) or record.fhir_resource_id
    reason = perfil.reason_of(request)
    service = _service_of(request)
    referral = referrals_service.create_external(
        db,
        patient=patient,
        specialist=specialist,
        motivo=f"{reason} (servicio solicitado: {service})" if service else reason,
        origen=label,
        referencia=correlation,
        idempotency_key=f"{BUNDLE_KEY_PREFIX}{record.fhir_resource_id}",
        ip_address=ip,
    )

    encounter = fhir_inbound.target_encounter(db, patient.id)
    observations = reports = 0
    for index, resource in enumerate(resources):
        kind = resource["resourceType"]
        if kind == "DiagnosticReport":
            db.add(PartnerDiagnosticReport(referral_id=referral.id, patient_id=patient.id,
                                           resource=resource))
            reports += 1
        elif kind == "Observation" and encounter is not None:
            parsed = parse_observation(resource)
            if not parsed.has_value:
                continue
            identifiers = resource.get("identifier")
            own = str(_dict(identifiers[0] if isinstance(identifiers, list) and identifiers else {})
                      .get("value") or "")
            external_id = f"{label}/{own}"[:120] if own else f"{record.fhir_resource_id}#{index}"[:120]
            if db.scalar(select(Observation.id).where(
                    Observation.patient_id == patient.id, Observation.fhir_resource_id == external_id)):
                continue  # the same partner observation already arrived with another referral
            db.add(Observation(
                patient_id=patient.id, encounter_id=encounter.id, status=parsed.status,
                code_system=parsed.code_system, code=parsed.code, display=parsed.display,
                value_numeric=parsed.value_numeric, value_text=parsed.value_text, unit=parsed.unit,
                observed_at=parsed.observed_at, origin=ObservationOrigin.FHIR,
                fhir_resource_id=external_id, created_by=None,
            ))
            observations += 1

    record.patient_id = patient.id
    record.outcome = FhirInboundOutcome.PROCESSED
    was_pending = record.estado_conciliacion == receptor.PENDING
    receptor._mark_reconciled(record, reconciled_by)  # noqa: SLF001
    receptor.audit_event(
        db,
        AuditAction.PATIENT_RECONCILED if was_pending and reconciled_by else AuditAction.INTEROP_RECEIVED,
        client_id=label, ip=ip, status=201, resource_type="Bundle", resultado="asociada",
        entity_type=patient.__tablename__, entity_id=patient.id, actor=reconciled_by,
        paciente_id=str(patient.id), remision_id=str(referral.id), especialista_id=str(specialist.id),
        evento_id=str(record.id), solicitud=correlation, observaciones=observations, informes=reports,
    )
    commit(db)
    referrals_service.announce_created(db, referral, origen=label)
    return referral


def _receive_bundle(db: Session, client: InteropClient, bundle: dict[str, Any], ip: str | None) -> JSONResponse:
    resources = validate_bundle(bundle, client.client_id)  # nothing is written before this passes
    if not federado.enabled():
        raise receptor._reject(  # noqa: SLF001
            503, "transient", "El receptor no tiene configurado el identificador federado "
            "(FEDERATED_SECRET): no puede asociar el mensaje a un paciente", "federado_desactivado",
            client_id=client.client_id)
    request = next(r for r in resources if r["resourceType"] == "ServiceRequest")
    federated_id = perfil.subject_federated_id(request)
    correlation = request_identifier(request) or ""
    resource_id = f"{client.client_id}/{correlation}"[:120]
    version = str(_dict(bundle.get("meta")).get("versionId") or "1")[:80]
    if db.scalar(fhir_inbound.inbound_event_stmt("Bundle", resource_id, version)) is not None:
        raise receptor._reject(  # noqa: SLF001
            409, "duplicate", "Bundle ya recibido (misma remision)", "recurso_repetido",
            client_id=client.client_id)

    patient = federado.patient_by_federated_id(db, federated_id)
    specialist = receptor.choose_specialist(db, request)
    reason = (
        receptor.REASON_UNKNOWN_PATIENT if patient is None
        else receptor.REASON_NO_SPECIALIST if specialist is None else None
    )
    # A document identifier is replaced by the federated id it stands for (or dropped when the
    # federated id is already there): the national number is used to find the patient and is
    # never stored. ONE canonical copy feeds the inbound event AND the stored resources.
    canonical = perfil.canonical_resource(bundle)
    resources = [entry["resource"] for entry in canonical["entry"]]
    record = receptor._new_record(  # noqa: SLF001
        client, "Bundle", resource_id, version, canonical, federated_id, patient, reason)
    try:
        if reason is not None:
            return receptor._queue(  # noqa: SLF001
                db, record, ip=ip, resource_type="Bundle", what="Una remision con su evidencia")
        db.add(record)
        db.flush()
        referral = finish_bundle(db, record, resources, patient, specialist, ip)
    except (ConflictError, IntegrityError):
        db.rollback()
        raise receptor._reject(  # noqa: SLF001
            409, "duplicate", "Bundle ya recibido (misma remision)", "recurso_repetido",
            client_id=client.client_id) from None
    body = operation_outcome([Issue(
        "informational", f"Bundle recibido; remision {referral.id} en la bandeja del especialista",
        severity="information")])
    body["id"] = str(referral.id)
    return JSONResponse(status_code=201, content=body, media_type=FHIR_JSON)


# ---------------------------------------------------------------------------
# A: receive the answer of B
# ---------------------------------------------------------------------------


def _receive_task(db: Session, client: InteropClient, task: dict[str, Any], ip: str | None) -> JSONResponse:
    issues = validate_task(task)
    if issues:
        raise receptor.Rejection(422, issues, "perfil", client_id=client.client_id)
    correlation = str(task["focus"]["identifier"]["value"]).strip()
    reason = _clip(_dict(task.get("statusReason")).get("text"))
    return _apply_answer(
        db, client, correlation=correlation, new_status=TASK_STATUSES[task["status"]],
        reason=reason, payload=task, resource_type="Task", ip=ip,
    )


# A ServiceRequest that answers one of OUR referrals (the docx allows "Task o actualizacion
# del ServiceRequest"): ``active`` means accepted, ``revoked`` means rejected.
REPLY_STATUSES = {"active": STATUS_ACCEPTED, "revoked": STATUS_REJECTED}


def reply_reason(resource: dict[str, Any]) -> str:
    """The reason of a ServiceRequest reply: ``statusReason`` (R5 style), ``note`` or ``reasonCode``."""

    text = str(_dict(resource.get("statusReason")).get("text") or "").strip()
    if text:
        return _clip(text)
    notes = resource.get("note")
    for note in notes if isinstance(notes, list) else []:
        text = str(_dict(note).get("text") or "").strip()
        if text:
            return _clip(text)
    return _clip(perfil.reason_of(resource))


def find_outbound_for_reply(
    db: Session, client: InteropClient, resource: dict[str, Any]
) -> OutboundReferral | None:
    """The referral of ``client`` that this ServiceRequest answers, or ``None`` for a new referral.

    A reply is told from a new incoming referral by its identifier: the ``identifier`` of the
    system we assign (or the ``id``, which we also set) equals the ``service_request_id`` of an
    outbound referral sent to THIS partner. A match that belongs to another partner is not a
    reply (it can never answer somebody else's referral).
    """

    correlation = request_identifier(resource)
    if correlation is None:
        return None
    return db.scalar(
        select(OutboundReferral).where(
            OutboundReferral.service_request_id == correlation,
            OutboundReferral.partner_id == client.id,
        )
    )


def validate_reply(resource: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    status = resource.get("status")
    if status not in REPLY_STATUSES:
        issues.append(Issue("value", "La respuesta a una remision enviada usa ServiceRequest.status "
                            "active (aceptada) o revoked (rechazada)", ("ServiceRequest.status",)))
    elif status == "revoked" and not reply_reason(resource):
        issues.append(Issue("required", "El motivo del rechazo es obligatorio (statusReason.text, "
                            "note.text o reasonCode.text)", ("ServiceRequest.statusReason.text",)))
    return issues


def receive_reply(
    db: Session, client: InteropClient, resource: dict[str, Any], outbound: OutboundReferral, ip: str | None
) -> JSONResponse:
    """A ServiceRequest update from the partner: treated exactly like a ``Task`` answer."""

    issues = validate_reply(resource)
    if issues:
        raise receptor.Rejection(422, issues, "perfil", client_id=client.client_id)
    return _apply_answer(
        db, client, correlation=outbound.service_request_id, new_status=REPLY_STATUSES[resource["status"]],
        reason=reply_reason(resource), payload=resource, resource_type="ServiceRequest", ip=ip,
    )


def _apply_answer(
    db: Session, client: InteropClient, *, correlation: str, new_status: str, reason: str,
    payload: dict[str, Any], resource_type: str, ip: str | None,
) -> JSONResponse:
    """Record the partner's answer on the outbound referral (Task or ServiceRequest reply)."""

    outbound = db.scalar(
        select(OutboundReferral)
        .where(OutboundReferral.service_request_id == correlation)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # Unknown and "someone else's" look alike: a partner learns nothing about other referrals.
    if outbound is None or outbound.partner_id != client.id:
        raise receptor._reject(  # noqa: SLF001
            404, "not-found", "Remision no encontrada para este socio", "remision_desconocida",
            client_id=client.client_id)

    if outbound.status == new_status:  # a replay of the answer we already hold: idempotent
        return informational(200, "Respuesta ya procesada (repetida)")
    if outbound.status not in AWAITING_ANSWER:
        raise receptor._reject(  # noqa: SLF001
            409, "conflict", f"La remision ya tiene la respuesta {outbound.status}", "respuesta_en_conflicto",
            client_id=client.client_id)

    outbound.status = new_status
    outbound.response_summary = reason or None
    outbound.response_task = payload
    receptor.audit_event(
        db, AuditAction.INTEROP_RECEIVED, client_id=client.client_id, ip=ip, status=200,
        resource_type=resource_type, resultado=new_status, entity_type="outbound_referrals",
        entity_id=outbound.id, paciente_id=str(outbound.patient_id), solicitud=correlation,
    )
    commit(db)

    patient = db.get(Patient, outbound.patient_id)
    who = f"{patient.first_name} {patient.last_name}" if patient is not None else "el paciente"
    verb = "acepto" if new_status == STATUS_ACCEPTED else "rechazo"
    message = f"{client.name} {verb} la remision de {who}" + (f": {reason}" if reason else "")
    events.publish(
        events.DomainEvent(
            tipo=EVENT_RESPONSE,
            mensaje=message[:480],
            recipients=(outbound.doctor_id,),
            paciente_id=outbound.patient_id,
            datos={
                "origen": "externo",
                "cliente": client.client_id,
                "remision_externa_id": str(outbound.id),
                "estado": new_status,
                "motivo_rechazo": reason or None,
            },
        ),
        db,
    )
    return informational(200, f"Respuesta registrada: remision {new_status}")


def receive(
    db: Session, client: InteropClient, raw: bytes, resource_type: str, ip: str | None
) -> JSONResponse:
    """Controls 3 to 5 for ``Bundle`` and ``Task`` (token and signature already passed)."""

    try:
        resource = json.loads(raw)
    except ValueError:
        resource = None
    if not isinstance(resource, dict):
        raise receptor._reject(  # noqa: SLF001
            422, "structure", "El cuerpo debe ser un recurso FHIR en JSON valido", "json_invalido",
            client_id=client.client_id)
    if resource_type == "Bundle":
        return _receive_bundle(db, client, resource, ip)
    return _receive_task(db, client, resource, ip)


# ---------------------------------------------------------------------------
# A: send a referral to a partner
# ---------------------------------------------------------------------------


class _LazySender:
    """Builds the configured sender only when consent already allowed the transport."""

    def __init__(self, db: Session, client_id: str) -> None:
        self._db = db
        self._client_id = client_id

    def enviar_bundle(self, resource: dict[str, Any]) -> SendResult:
        sender = None
        try:
            sender = destinations.sender_for_partner(self._db, self._client_id)
            return sender.enviar_bundle(resource)
        except SenderError as exc:
            return SendResult("Bundle", exc.status, False, str(exc), 0)
        except Exception as exc:  # noqa: BLE001 -- any transport bug is audited as a failed send, not a 500
            logger.exception("Bundle delivery to %s failed", self._client_id)
            return SendResult("Bundle", 0, False, f"Error de envio: {exc.__class__.__name__}", 0)
        finally:
            _close(sender)


def _close(sender: Any) -> None:
    """Release the HTTP client of a sender; a failure to close never hides the real outcome."""

    close = getattr(sender, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001
        logger.warning("Could not close the partner sender", exc_info=True)


SETTLEABLE = (STATUS_SENDING, STATUS_SEND_ERROR)


def _settle(db: Session, outbound: OutboundReferral, status: str, summary: str | None) -> str:
    """Move a record out of ``enviando`` (or ``error_envio``) with the outcome of OUR send.

    The row is locked first (``FOR UPDATE``; a no-op on SQLite) and read again from the
    database, so it cannot race ``_receive_task``: a partner answer that already moved the
    record to ``aceptada`` or ``rechazada`` is NEVER overwritten by ``enviada`` or an error.
    Returns the REAL state of the row after the settle.
    """

    row = db.scalar(
        select(OutboundReferral)
        .where(OutboundReferral.id == outbound.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is not None and row.status in SETTLEABLE:
        row.status = status
        row.response_summary = summary
    real = row.status if row is not None else status
    commit(db)  # releases the lock whether or not anything changed
    return real


def refer_to_partner(
    db: Session,
    *,
    actor: User,
    patient_id: uuid.UUID,
    client_id: str,
    reason: str,
    service: str,
    priority: str,
) -> OutboundReferral:
    """Send the referral Bundle and keep the local outbound record.

    Order: role and patient scope, consent (R32), Bundle, then the record is committed as
    ``enviando`` BEFORE the transport is used, so a partner that commits the Bundle but whose
    answer never reaches us (timeout) can still match its later Task. The record then becomes
    ``enviada``; ``rechazada_por_validacion`` when the partner answers 400 or 422; or
    ``error_envio`` for any other failure (the record and its ServiceRequest identifier stay,
    and the call raises a gateway error).
    """

    reason = (reason or "").strip()
    service = (service or "").strip()
    if not reason or not service:
        raise InvalidValueError("La remision exige motivo y servicio solicitado")
    if priority not in PRIORITIES:
        raise InvalidValueError(f"Prioridad desconocida: use {', '.join(PRIORITIES)}")
    request_id = f"sr-{uuid.uuid4().hex}"
    # The reason and the service are free text typed by the doctor: what travels has the
    # patient's name, document, phone, e-mail and address removed. The local record keeps
    # the original wording (our own database, shown to our own doctor).
    patient = db.get(Patient, patient_id)
    order = build_referral_request(
        request_id, scrub_for_partner(reason, patient), scrub_for_partner(service, patient), priority)
    outbound: OutboundReferral | None = None

    def persist_before_sending() -> None:
        nonlocal outbound
        partner = db.scalar(select(InteropClient).where(InteropClient.client_id == client_id))
        if partner is None:
            return  # the sender refuses an unregistered partner; there is nobody to correlate
        row = OutboundReferral(
            partner_id=partner.id, patient_id=patient_id, doctor_id=actor.id,
            service_request_id=request_id, status=STATUS_SENDING, reason=reason[:1000],
            service=service[:200], priority=priority,
        )
        db.add(row)
        commit(db)
        outbound = row

    try:
        result = envio.send_to_partner(
            db, actor=actor, patient_id=patient_id, client_id=client_id,
            sender=_LazySender(db, client_id), service_request=order,
            limits=(MAX_ENTRIES, receptor.MAX_BODY_BYTES), before_send=persist_before_sending,
        )
    except Exception:
        if outbound is not None:  # never leave a record stuck in ``enviando``
            db.rollback()
            _settle(db, outbound, STATUS_SEND_ERROR, "Error inesperado durante el envio")
        raise
    if result.entregado:
        status, summary = STATUS_SENT, None
    elif result.status in (400, 422):
        status, summary = STATUS_INVALID, _clip(result.mensaje)
    else:
        status, summary = STATUS_SEND_ERROR, _clip(result.mensaje)
    if outbound is None:
        raise FhirGatewayError(f"El socio no recibio la remision: {_clip(result.mensaje, 300)}")
    # A Task that landed during the send already moved the row to aceptada/rechazada: that is
    # a success, whatever the transport said. Only a row still in error_envio is a failure.
    if _settle(db, outbound, status, summary) == STATUS_SEND_ERROR:
        raise FhirGatewayError(f"El socio no recibio la remision: {_clip(result.mensaje, 300)}")
    return outbound


def list_outbound(db: Session, user: User, *, limit: int = 200) -> list[tuple[OutboundReferral, str]]:
    stmt = (
        select(OutboundReferral, InteropClient.client_id)
        .join(InteropClient, InteropClient.id == OutboundReferral.partner_id)
        .order_by(OutboundReferral.created_at.desc(), OutboundReferral.id)
        .limit(limit)
    )
    if user.role.code != RoleCode.ADMIN:
        stmt = stmt.where(OutboundReferral.doctor_id == user.id)
    return [(row, client_id) for row, client_id in db.execute(stmt)]


def get_outbound(db: Session, user: User, outbound_id: uuid.UUID) -> OutboundReferral:
    row = db.get(OutboundReferral, outbound_id)
    if row is None or (user.role.code != RoleCode.ADMIN and row.doctor_id != user.id):
        raise NotFoundError("Remision enviada no encontrada")
    return row


# ---------------------------------------------------------------------------
# B: answer A after the decision (outbox + retry)
# ---------------------------------------------------------------------------


def _audit_task_delivery(db: Session, row: InteropTaskOutbox, result: SendResult, *, retry: bool) -> None:
    trail.audit(
        db,
        action=AuditAction.INTEROP_SENT,
        entity_type="interop_task_outbox",
        entity_id=row.id,
        actor=None,
        metadata={
            "origen": "saludred",
            "cliente": row.client_id,
            "recurso": "Task",
            "codigo_http": result.status,
            "entregado": result.entregado,
            "intento": row.attempts,
            "reintento": retry,
            "referral_id": str(row.referral_id),
        },
    )


def _is_permanent(result: SendResult, *, token_stage: bool = False) -> bool:
    """A 4xx answer of the partner's Task endpoint will not change by sending the same Task
    again, except 408, 429 and the credential failures 401 and 403.

    A failure at the TOKEN stage (``SenderError`` from ``obtener_token``: a 401 or 403 from
    the partner's ``/oauth/token``) is never permanent: it says nothing about this Task, and
    the credentials or the partner's configuration may be fixed before the next attempt. The
    same holds for a 401 or 403 from the Task endpoint itself (a wrong API key or a missing
    scope): the partner can fix it, so it is retried up to the attempt cap.
    """

    if token_stage:
        return False
    return 400 <= result.status < 500 and result.status not in RETRYABLE_CLIENT_ERRORS + CREDENTIAL_ERRORS


def _deliver(
    db: Session, row: InteropTaskOutbox, *, retry: bool, senders: dict[str, Any] | None = None
) -> bool:
    """One attempt. The outcome (delivered or failed) and its audit row are committed.

    ``senders`` is a per-batch cache by ``client_id``: its senders are reused and the CALLER
    closes them. Without it the sender built for this attempt is closed here.
    """

    sender = senders.get(row.client_id) if senders is not None else None
    owned = False
    token_stage = False
    try:
        if sender is None:
            sender = destinations.sender_for_partner(db, row.client_id)
            if senders is not None:
                senders[row.client_id] = sender
            else:
                owned = True
        result = sender.enviar_task(row.resource)
    except SenderError as exc:
        # Raised before the Task was posted (no sender configured, or the token request failed).
        token_stage = True
        result = SendResult("Task", exc.status, False, str(exc), 0)
    except Exception as exc:  # noqa: BLE001 -- a transport bug must never reach the specialist
        logger.exception("Task delivery to %s failed", row.client_id)
        result = SendResult("Task", 0, False, f"Error de envio: {exc.__class__.__name__}", 0)
    finally:
        if owned:
            _close(sender)
    row.attempts += 1
    if result.entregado:
        row.delivered_at = _now()
        row.last_error = None
    else:
        row.last_error = _clip(result.mensaje)
    _audit_task_delivery(db, row, result, retry=retry)
    if not result.entregado and _is_permanent(result, token_stage=token_stage):
        # No schema change: the existing columns carry the verdict. The row leaves the retry
        # queue (attempts at the cap) and last_error says why.
        row.attempts = max(row.attempts, MAX_DELIVERY_ATTEMPTS)
        row.last_error = (PERMANENT_PREFIX + _clip(result.mensaje))[:2000]
    db.commit()
    return result.entregado


def expects_answer(referral: Referral) -> bool:
    """Only referrals that arrived as a Bundle expect a Task; a lone ServiceRequest (the
    older partner flow) has nobody waiting for one."""

    return bool(
        referral.origen_externo and referral.referencia_externa
        and (referral.idempotency_key or "").startswith(BUNDLE_KEY_PREFIX)
    )


def stage_answer(db: Session, referral: Referral) -> InteropTaskOutbox | None:
    """Add the Task to the outbox INSIDE the caller's transaction; the caller commits.

    ``referrals.accept`` and ``referrals.reject`` call this just before their own commit, so
    the status change and the Task row are ONE unit: either both are stored or neither (if
    this raises, the decision is rolled back and the specialist can try again). Nothing is
    sent here; delivery stays in the background task. ``None`` when nobody waits for a Task.
    """

    if not expects_answer(referral):
        return None
    row = db.scalar(select(InteropTaskOutbox).where(InteropTaskOutbox.referral_id == referral.id))
    if row is None:
        patient = db.get(Patient, referral.patient_id)
        row = InteropTaskOutbox(
            referral_id=referral.id, client_id=referral.origen_externo,
            resource=build_task(referral, patient), attempts=0,
        )
        db.add(row)
    db.flush()  # the id exists, and a failing insert surfaces here, before the commit
    return row


def undelivered_answer_id(db: Session, referral: Referral) -> uuid.UUID | None:
    """The id of the committed outbox row to deliver for ``referral``, or ``None``. Never raises."""

    if not expects_answer(referral):
        return None
    try:
        row = db.scalar(select(InteropTaskOutbox).where(InteropTaskOutbox.referral_id == referral.id))
        return None if row is None or row.delivered_at is not None else row.id
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the outbox row of referral %s", referral.id)
        db.rollback()
        return None


def queue_answer(db: Session, referral: Referral) -> uuid.UUID | None:
    """Stage and COMMIT the Task on its own, for callers that did not use the hook
    (scripts, tests, ``notify_origin``). The routes use ``stage_answer`` inside the
    referral's own transaction instead. Never raises."""

    if not expects_answer(referral):
        return None
    try:
        stage_answer(db, referral)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the answer to the partner for referral %s", referral.id)
        db.rollback()
        return None
    return undelivered_answer_id(db, referral)


# Test seam: a session factory. In production it is None and ``database.SessionLocal`` is used.
_session_factory: Callable[[], Session] | None = None


def configure(*, session_factory: Callable[[], Session] | None = None) -> None:
    global _session_factory
    _session_factory = session_factory


def deliver_queued(outbox_id: uuid.UUID) -> bool:
    """Background task: send one queued Task with its OWN session (the request's is closed).

    A slow or dead partner only delays this task, never the specialist's response. Never
    raises; a failure stays in the outbox for ``retry_outbox``.
    """

    from app.core import database

    db = (_session_factory or database.SessionLocal)()
    try:
        row = db.get(InteropTaskOutbox, outbox_id)
        if row is None or row.delivered_at is not None:
            return row is not None
        return _deliver(db, row, retry=False)
    except Exception:  # noqa: BLE001
        logger.exception("Could not answer the partner for outbox row %s", outbox_id)
        db.rollback()
        return False
    finally:
        db.close()


def notify_origin(db: Session, referral: Referral) -> bool:
    """Queue and send the Task inline with the given session (scripts and tests).

    The accept/reject routes use ``queue_answer`` + ``deliver_queued`` in the background
    instead. Returns ``True`` when the Task was delivered. Never raises.
    """

    outbox_id = queue_answer(db, referral)
    if outbox_id is None:
        return expects_answer(referral) and _delivered(db, referral)
    try:
        row = db.get(InteropTaskOutbox, outbox_id)
        return _deliver(db, row, retry=False)
    except Exception:  # noqa: BLE001
        logger.exception("Could not answer the partner for referral %s", referral.id)
        db.rollback()
        return False


def _delivered(db: Session, referral: Referral) -> bool:
    row = db.scalar(select(InteropTaskOutbox).where(InteropTaskOutbox.referral_id == referral.id))
    return row is not None and row.delivered_at is not None


def pending_outbox(db: Session, limit: int = 50) -> list[InteropTaskOutbox]:
    """Undelivered Tasks still worth sending. Fewest attempts first, so a row that keeps
    failing cannot starve newer ones; rows at the cap (or marked permanent) are left out."""

    return list(db.scalars(
        select(InteropTaskOutbox)
        .where(InteropTaskOutbox.delivered_at.is_(None),
               InteropTaskOutbox.attempts < MAX_DELIVERY_ATTEMPTS)
        .order_by(InteropTaskOutbox.attempts, InteropTaskOutbox.created_at, InteropTaskOutbox.id)
        .limit(limit)
    ))


def retry_outbox(db: Session, *, limit: int = 50) -> dict[str, int]:
    """Send again every undelivered Task (admin endpoint or a scheduled call).

    One sender per partner for the whole batch, closed at the end.
    """

    delivered = failed = 0
    senders: dict[str, Any] = {}
    try:
        for row in pending_outbox(db, limit):
            if _deliver(db, row, retry=True, senders=senders):
                delivered += 1
            else:
                failed += 1
    finally:
        for sender in senders.values():
            _close(sender)
    return {"entregadas": delivered, "fallidas": failed}
