"""Receiver of the partner messages: one message, five controls, in this order (slide 31).

1. **Token** (who sends): 401 when missing or invalid, 403 when the token lacks the scope
   of the route (``system/Observation.write`` / ``system/ServiceRequest.write``). With
   ``INTEROP_ALLOW_API_KEY`` the header ``X-Api-Key: <client_id>:<client_secret>`` is an
   alternative to the Bearer token, on these routes only.
2. **Signature** (it arrived intact): 401 for a bad signature or a date outside the
   window, 409 for a nonce already seen. ``INTEROP_REQUIRE_SIGNATURE=false`` accepts a
   message with no signature headers at all (date and nonce checks are skipped with it);
   a message that carries a signature is always verified.
3. **Profile** (it means the same): 422 with an ``OperationOutcome``.
4. **Patient** (to whom it goes): 201 when the federated id resolves to a patient, 202
   when it does not (or a referral has nobody to receive it). A result for a patient we
   do not find is NOT discarded: it waits in the reconciliation queue
   (``fhir_inbound_events``) and the admins are told. Losing a lab result is worse than
   reviewing it.
5. **Reaction** (what happens): live notification, critical alert for a critical value,
   the referral in the specialist's inbox, and the audit row with an external origin.

The order matters: it makes no sense to validate the content of a message whose origin
was not checked. The body is therefore read only after the token, and parsed only after
the signature. Every rejection is audited (``INTEROP_REJECTED``) with client, IP and
status; every message that gets through, ``INTEROP_RECEIVED``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import events
from app.core.config import get_settings
from app.core.net import client_ip
from app.fhir.parsing import FhirParseError, parse_observation
from app.models.clinical import Observation
from app.models.enums import AuditAction, FhirInboundOutcome, ObservationOrigin, ObservationStatus
from app.models.governance import FhirInboundEvent
from app.models.identity import User
from app.models.interop import InteropClient
from app.models.patient import Patient
from app.services import alerts, fhir_inbound, recipients, trail
from app.services import referrals as referrals_service
from app.services.errors import ConflictError, commit
from app.services.interop import clientes, federado, firma, perfil
from app.services.interop.outcome import Issue, informational, respond

logger = logging.getLogger(__name__)
security_log = logging.getLogger("app.security")

MAX_BODY_BYTES = 262_144
MAX_LABEL_CHARS = 80  # client_id and other header-derived text kept in audit metadata
UNAUTH_AUDIT_WINDOW = 60.0
MAX_UNAUTH_KEYS = 5_000
_UNAUTH_SEEN: dict[tuple[str, str], float] = {}
_UNAUTH_LOCK = threading.Lock()


def _now() -> float:
    return time.monotonic()

SCOPE_BY_RESOURCE = {
    "Observation": clientes.SCOPE_OBSERVATION,
    "ServiceRequest": clientes.SCOPE_SERVICE_REQUEST,
    "Bundle": clientes.SCOPE_BUNDLE,
    "Task": clientes.SCOPE_TASK,
}
EVENT_OBSERVATION = "interop_observacion"
EVENT_PENDING = "interop_pendiente"

PENDING = "pendiente"
RECONCILED = "conciliado"
REASON_UNKNOWN_PATIENT = "paciente_desconocido"
REASON_NO_SPECIALIST = "sin_especialista"


class Rejection(Exception):
    """The message is refused with ``status``; the issues become the OperationOutcome."""

    def __init__(
        self,
        status: int,
        issues: list[Issue],
        motivo: str,
        *,
        client_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(motivo)
        self.status = status
        self.issues = issues
        self.motivo = motivo
        self.client_id = client_id
        self.headers = headers


def _reject(
    status: int, code: str, diagnostics: str, motivo: str, *, client_id: str | None = None,
    expression: tuple[str, ...] = (), headers: dict[str, str] | None = None,
) -> Rejection:
    return Rejection(
        status, [Issue(code, diagnostics, expression)], motivo, client_id=client_id, headers=headers
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def clip(value: str | None, limit: int = MAX_LABEL_CHARS) -> str | None:
    """Attacker-controlled text (client_id, header values) is bounded before it is stored or logged."""

    return value[:limit] if isinstance(value, str) else value


def should_audit_unauthenticated(
    ip: str | None, motivo: str, *, status: int, client_id: str | None
) -> bool:
    """Aggregate the rejections of callers that proved nothing.

    Every one goes to the ``app.security`` log (cheap, rotated); the audit table gets at
    most ONE ``INTEROP_REJECTED`` row per (ip, reason) per ``UNAUTH_AUDIT_WINDOW`` seconds,
    so an unauthenticated flood cannot fill the hash-chained audit log.
    """

    security_log.warning(
        "interop_rechazo ip=%s motivo=%s estado=%s cliente=%r",
        ip or "-", motivo, status, clip(client_id),
    )
    key = (ip or "-", motivo)
    now = _now()
    with _UNAUTH_LOCK:
        last = _UNAUTH_SEEN.get(key)
        if last is not None and now - last < UNAUTH_AUDIT_WINDOW:
            return False
        if len(_UNAUTH_SEEN) >= MAX_UNAUTH_KEYS:
            for stale in [k for k, t in _UNAUTH_SEEN.items() if now - t >= UNAUTH_AUDIT_WINDOW]:
                del _UNAUTH_SEEN[stale]
        if len(_UNAUTH_SEEN) < MAX_UNAUTH_KEYS:
            _UNAUTH_SEEN[key] = now
        return True  # table full of fresh keys: fail open, audit rather than lose the row


def reset_unauthenticated_audit() -> None:
    with _UNAUTH_LOCK:
        _UNAUTH_SEEN.clear()


def audit_event(
    db: Session,
    action: AuditAction,
    *,
    client_id: str | None,
    ip: str | None,
    status: int,
    resource_type: str,
    resultado: str,
    entity_type: str = "interop",
    entity_id: uuid.UUID | None = None,
    actor: User | None = None,
    **extra: Any,
) -> None:
    """One audit row marked as external origin, with client, IP and HTTP status."""

    trail.audit(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        metadata={
            "origen": "externo",
            "cliente": clip(client_id),
            "ip": ip,
            "codigo_http": status,
            "recurso": resource_type,
            "resultado": resultado,
            **extra,
        },
        ip_address=ip,
    )


def _audit_rejection(
    db: Session, rejection: Rejection, client_id: str | None, ip: str | None, resource_type: str
) -> None:
    if client_id is None and not should_audit_unauthenticated(
        ip, rejection.motivo, status=rejection.status, client_id=client_id
    ):
        return  # an unauthenticated repeat inside the window: log only
    try:
        db.rollback()
        audit_event(
            db, AuditAction.INTEROP_REJECTED, client_id=client_id, ip=ip, status=rejection.status,
            resource_type=resource_type, resultado="rechazado", motivo=rejection.motivo,
        )
        db.commit()
    except Exception:  # noqa: BLE001 -- the answer to the partner must not depend on the audit
        logger.exception("Could not audit the rejected interop message")
        db.rollback()


# ---------------------------------------------------------------------------
# 1. Token
# ---------------------------------------------------------------------------


API_KEY_HEADER = "X-Api-Key"
API_KEY_SEPARATOR = ":"  # cannot appear in a client_id ([a-z0-9._-])


def authenticate_api_key(db: Session, api_key: str, resource_type: str) -> InteropClient:
    """The partner behind ``X-Api-Key: <client_id>:<client_secret>``, with the scope of this route.

    Only for the /interop/* entry routes and only with ``INTEROP_ALLOW_API_KEY``. The secret is
    checked against the SAME hashed ``client_secret`` of ``interop_clients`` that the token
    endpoint uses (the hash verification is constant-time, and an unknown client spends the
    same time as a wrong secret). Scopes and ``is_active`` are those of the registration.
    """

    challenge = {"WWW-Authenticate": "Bearer"}
    client_id, separator, secret = api_key.strip().partition(API_KEY_SEPARATOR)
    try:
        # A malformed key costs the same as a wrong one: the answer time says nothing.
        client = clientes.authenticate_client(
            db, client_id.strip().lower() if separator else None, secret if separator else api_key)
    except clientes.TokenError:
        raise _reject(401, "login", "API key invalida o cliente deshabilitado", "api_key_invalida",
                      headers=challenge) from None
    needed = SCOPE_BY_RESOURCE[resource_type]
    if needed not in set(client.scopes):
        raise _reject(403, "forbidden", f"El cliente no tiene el alcance {needed}", "sin_alcance",
                      client_id=client.client_id)
    clientes.touch_last_used(db, client)  # at most once per minute per client
    return client


def authenticate(
    db: Session, authorization: str | None, resource_type: str, api_key: str | None = None
) -> InteropClient:
    """The partner behind a Bearer system token (or, when enabled, an API key), with the
    scope of this route. A Bearer token always wins when both are sent."""

    challenge = {"WWW-Authenticate": "Bearer"}
    scheme, _, supplied = (authorization or "").partition(" ")
    if api_key and not (scheme.lower() == "bearer" and supplied.strip()):
        if not get_settings().interop_allow_api_key:
            raise _reject(401, "login", f"La autenticacion con {API_KEY_HEADER} no esta habilitada "
                          "en este receptor: use el token OAuth (Authorization: Bearer)",
                          "api_key_desactivada", headers=challenge)
        return authenticate_api_key(db, api_key, resource_type)
    if scheme.lower() != "bearer" or not supplied.strip():
        raise _reject(401, "login", "Falta el token del sistema (Authorization: Bearer)",
                      "sin_token", headers=challenge)
    claims = clientes.decode_system_token(supplied.strip())
    if claims is None:
        raise _reject(401, "login", "Token invalido o vencido", "token_invalido", headers=challenge)
    client = clientes.get_active_client(db, claims["sub"])
    if client is None:
        raise _reject(401, "login", "El cliente no existe o esta deshabilitado",
                      "cliente_inactivo", headers=challenge)
    # The token can never exceed what is registered NOW (a scope removed after the token
    # was issued stops working at once, like a role change for a user).
    granted = set(claims["scope"].split()) & set(client.scopes)
    needed = SCOPE_BY_RESOURCE[resource_type]
    if needed not in granted:
        raise _reject(403, "forbidden", f"El token no incluye el alcance {needed}",
                      "sin_alcance", client_id=client.client_id)
    return client


# ---------------------------------------------------------------------------
# 2. Signature
# ---------------------------------------------------------------------------


def signature_headers_present(headers: Any) -> bool:
    return any(headers.get(name) for name in (firma.HEADER_DATE, firma.HEADER_NONCE, firma.HEADER_SIGNATURE))


def verify_signature(db: Session, client: InteropClient, headers: Any, raw: bytes) -> None:
    """Control 2. Required by default (``INTEROP_REQUIRE_SIGNATURE``); a deployment that agreed
    to a looser contract with its partner may turn the requirement off. Then a message with NO
    signature header is accepted and the date and nonce checks are skipped with it, while any
    message that does carry (part of) a signature is still fully verified."""

    if not get_settings().interop_require_signature and not signature_headers_present(headers):
        security_log.info("interop_sin_firma cliente=%r", clip(client.client_id))
        return
    try:
        secret = clientes.open_signing_secret(client)
    except clientes.SigningSecretUnavailableError:
        logger.error("The signing secret of client %s cannot be read", client.client_id)
        raise _reject(503, "transient", "El receptor no puede leer el secreto de firma de este cliente",
                      "secreto_firma_ilegible", client_id=client.client_id) from None
    try:
        signed = firma.parse_headers(
            headers.get(firma.HEADER_DATE), headers.get(firma.HEADER_NONCE),
            headers.get(firma.HEADER_SIGNATURE),
        )
        firma.check(secret, signed, raw)
        firma.register_nonce(db, client.client_id, signed.nonce)
    except firma.SignatureError as exc:
        motivo = "mensaje_repetido" if exc.status == 409 else "firma_invalida"
        raise _reject(exc.status, "duplicate" if exc.status == 409 else "security",
                      exc.diagnostics, motivo, client_id=client.client_id) from None


# ---------------------------------------------------------------------------
# 3. Profile
# ---------------------------------------------------------------------------


def parse_json(raw: bytes, client_id: str) -> dict[str, Any]:
    try:
        resource = json.loads(raw)
    except ValueError:
        raise _reject(422, "structure", "El cuerpo no es JSON valido", "json_invalido",
                      client_id=client_id) from None
    if not isinstance(resource, dict):
        raise _reject(422, "structure", "El cuerpo debe ser un recurso FHIR (objeto JSON)",
                      "json_invalido", client_id=client_id)
    return resource


def parse_and_validate(raw: bytes, resource_type: str, client_id: str) -> dict[str, Any]:
    return validate_resource(parse_json(raw, client_id), resource_type, client_id)


def validate_resource(resource: dict[str, Any], resource_type: str, client_id: str) -> dict[str, Any]:
    issues = (
        perfil.validate_observation(resource)
        if resource_type == "Observation"
        else perfil.validate_service_request(resource)
    )
    if issues:
        raise Rejection(422, issues, "perfil", client_id=client_id)
    return resource


# ---------------------------------------------------------------------------
# 4 and 5. Patient and reaction
# ---------------------------------------------------------------------------


def _identity(client: InteropClient, resource: dict[str, Any], raw: bytes) -> tuple[str, str]:
    """Namespaced resource id and version: two partners may both send ``id: obs-1``."""

    digest = hashlib.sha256(raw).hexdigest()
    own_id = str(resource.get("id") or "").strip() or f"h-{digest[:24]}"
    version = str((resource.get("meta") or {}).get("versionId") or "").strip() or f"sha256:{digest[:32]}"
    return f"{client.client_id}/{own_id}"[:120], version[:80]


def _new_record(
    client: InteropClient, resource_type: str, resource_id: str, version: str,
    resource: dict[str, Any], federated_id: str, patient: Patient | None, reason: str | None,
) -> FhirInboundEvent:
    record = FhirInboundEvent(
        fhir_resource_type=resource_type,
        fhir_resource_id=resource_id,
        fhir_version_id=version,
        outcome=(
            FhirInboundOutcome.PROCESSED if patient is not None else FhirInboundOutcome.UNKNOWN_PATIENT
        ),
        patient_id=patient.id if patient is not None else None,
        resource=resource,
        origen_externo=client.client_id,
        id_federado_recibido=federated_id,
    )
    if reason is not None:
        record.estado_conciliacion = PENDING
        record.motivo_pendiente = reason
    return record


def _mark_reconciled(record: FhirInboundEvent, admin: User | None) -> None:
    if record.estado_conciliacion == PENDING:
        record.estado_conciliacion = RECONCILED
        record.conciliado_por = admin.id if admin is not None else None
        record.conciliado_en = datetime.now(timezone.utc)
        record.motivo_pendiente = None


def _notify_admins(db: Session, record: FhirInboundEvent, label: str, what: str) -> None:
    admins = tuple(db.scalars(recipients.admins_stmt()))
    reason = (
        "no se encontro al paciente" if record.motivo_pendiente == REASON_UNKNOWN_PATIENT
        else "no hay un especialista que la reciba"
    )
    events.publish(
        events.DomainEvent(
            tipo=EVENT_PENDING,
            mensaje=f"{what} de {label} pendiente de conciliacion: {reason}.",
            recipients=admins,
            paciente_id=None,
            datos={
                "origen": "externo",
                "cliente": label,
                "evento_id": str(record.id),
                "recurso": record.fhir_resource_type,
                "motivo": record.motivo_pendiente,
            },
            clinical=False,
        ),
        db,
    )


def finish_observation(
    db: Session,
    *,
    record: FhirInboundEvent,
    patient: Patient,
    ip: str | None,
    reconciled_by: User | None = None,
) -> bool:
    """Store the Observation, raise the critical alert, notify, audit. Returns ``stored``."""

    resource = record.resource
    label = record.origen_externo or "externo"
    try:
        parsed = parse_observation(resource)
    except FhirParseError as exc:  # the profile already accepted it: defensive only
        raise _reject(422, "structure", str(exc), "perfil", client_id=label) from exc
    code, value = perfil.observation_numbers(resource)

    observation: Observation | None = None
    encounter = fhir_inbound.target_encounter(db, patient.id)
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
            fhir_resource_id=record.fhir_resource_id,
            created_by=None,
        )
        db.add(observation)
        db.flush()
        record.observation_id = observation.id

    record.patient_id = patient.id
    record.outcome = FhirInboundOutcome.PROCESSED
    was_pending = record.estado_conciliacion == PENDING
    _mark_reconciled(record, reconciled_by)
    addressed = recipients.for_patient(db, patient)
    audit_event(
        db,
        AuditAction.PATIENT_RECONCILED if was_pending and reconciled_by else AuditAction.INTEROP_RECEIVED,
        client_id=label, ip=ip, status=201, resource_type="Observation", resultado="asociada",
        entity_type=patient.__tablename__, entity_id=patient.id, actor=reconciled_by,
        paciente_id=str(patient.id), codigo=code, almacenada=observation is not None,
        evento_id=str(record.id), destinatarios=[str(r) for r in addressed],
    )
    commit(db)

    unit = parsed.unit or perfil.PROFILE[code].unit
    # A cancelled / entered-in-error result is NOT a clinical finding: it is kept (and
    # audited) but it neither raises a critical alert nor notifies as a new result.
    withdrawn = parsed.status == ObservationStatus.CANCELLED
    critical = (
        not withdrawn
        and code == perfil.GLUCOSE_CODE
        and (
            value >= get_settings().interop_glucose_critical_high
            or value <= get_settings().interop_glucose_critical_low
        )
    )
    alert = None
    created = False
    if critical:
        alert, created = alerts.raise_critical_alert_from_observation(
            db, patient, label=parsed.label, code=code, value=value, unit=unit, origin=label,
            observation_id=observation.id if observation else None,
        )
    if not withdrawn:
        events.publish(
            events.DomainEvent(
                tipo=EVENT_OBSERVATION,
                mensaje=f"Nuevo resultado de laboratorio desde {label}: {parsed.describe()}",
                recipients=addressed,
                paciente_id=patient.id,
                datos={
                    "origen": "externo",
                    "cliente": label,
                    "codigo": code,
                    "nombre": parsed.label,
                    "valor": parsed.value_label or None,
                    "critico": critical,
                    "observacion_id": str(observation.id) if observation else None,
                    "almacenada": observation is not None,
                    "paciente_nombre": patient.full_name,
                },
            ),
            db,
        )
    if alert is not None and created:
        alerts.announce_critical(db, alert)
    return observation is not None


def finish_referral(
    db: Session,
    *,
    record: FhirInboundEvent,
    patient: Patient,
    specialist: User,
    ip: str | None,
    reconciled_by: User | None = None,
) -> referrals_service.Referral:
    """Create the referral in the specialist's inbox and audit it with its external origin."""

    resource = record.resource
    label = record.origen_externo or "externo"
    referral = referrals_service.create_external(
        db,
        patient=patient,
        specialist=specialist,
        motivo=perfil.reason_of(resource),
        origen=label,
        referencia=str(resource.get("id") or record.fhir_resource_id),
        idempotency_key=f"interop:{record.fhir_resource_id}",
        ip_address=ip,
    )
    record.patient_id = patient.id
    record.outcome = FhirInboundOutcome.PROCESSED
    was_pending = record.estado_conciliacion == PENDING
    _mark_reconciled(record, reconciled_by)
    audit_event(
        db,
        AuditAction.PATIENT_RECONCILED if was_pending and reconciled_by else AuditAction.INTEROP_RECEIVED,
        client_id=label, ip=ip, status=201, resource_type="ServiceRequest", resultado="asociada",
        entity_type=patient.__tablename__, entity_id=patient.id, actor=reconciled_by,
        paciente_id=str(patient.id), remision_id=str(referral.id),
        especialista_id=str(specialist.id), evento_id=str(record.id),
    )
    commit(db)
    referrals_service.announce_created(db, referral, origen=label)
    return referral


def _queue(
    db: Session, record: FhirInboundEvent, *, ip: str | None, resource_type: str, what: str
) -> JSONResponse:
    """202: the message is kept, the admins are told, nothing is lost."""

    label = record.origen_externo or "externo"
    db.add(record)
    try:
        db.flush()
    except IntegrityError:  # UNIQUE (type, id, version): a concurrent copy won
        # Only this: any other failure (connection, programming error) must surface
        # as a 5xx, not be mislabelled as a duplicate.
        db.rollback()
        raise _reject(409, "duplicate", "Recurso ya recibido (misma version)", "recurso_repetido",
                      client_id=label) from None
    audit_event(
        db, AuditAction.INTEROP_RECEIVED, client_id=label, ip=ip, status=202,
        resource_type=resource_type, resultado="pendiente_de_conciliacion",
        entity_type="fhir_inbound_events", entity_id=record.id,
        motivo=record.motivo_pendiente, id_federado=record.id_federado_recibido,
        evento_id=str(record.id),
    )
    commit(db)
    _notify_admins(db, record, label, what)
    return informational(
        202, f"Aceptada; pendiente de conciliacion ({record.motivo_pendiente}). Evento {record.id}"
    )


def choose_specialist(db: Session, resource: dict[str, Any]) -> User | None:
    """D6: the especialista of the target IPS, else ``INTEROP_DEFAULT_SPECIALIST``, else none."""

    return referrals_service.specialist_of_organization(
        db, perfil.performer_organization_code(resource)
    ) or referrals_service.resolve_default_specialist(db)


def process(
    db: Session, client: InteropClient, headers: Any, raw: bytes, resource_type: str, ip: str | None
) -> JSONResponse:
    """Controls 2 to 5 for a message whose token is already valid."""

    verify_signature(db, client, headers, raw)
    if resource_type in ("Bundle", "Task"):
        from app.services.interop.roundtrip import receive

        return receive(db, client, raw, resource_type, ip)
    resource = parse_json(raw, client.client_id)
    if resource_type == "ServiceRequest":
        from app.services.interop import roundtrip

        # An update of one of OUR outbound referrals is the partner's answer (docx: "Task o
        # actualizacion del ServiceRequest"), not a new referral. Told apart by the identifier.
        outbound = roundtrip.find_outbound_for_reply(db, client, resource)
        if outbound is not None:
            return roundtrip.receive_reply(db, client, resource, outbound, ip)
    resource = validate_resource(resource, resource_type, client.client_id)

    if not federado.enabled():
        raise _reject(
            503, "transient",
            "El receptor no tiene configurado el identificador federado (FEDERATED_SECRET): "
            "no puede asociar el mensaje a un paciente", "federado_desactivado",
            client_id=client.client_id, expression=("subject.identifier",),
        )
    federated_id = perfil.subject_federated_id(resource)
    patient = federado.patient_by_federated_id(db, federated_id)
    # Any-partner mode: a national document is only used to find the patient; the stored copy
    # of the message carries the federated id instead.
    resource = perfil.canonical_resource(resource)
    resource_id, version = _identity(client, resource, raw)
    if db.scalar(fhir_inbound.inbound_event_stmt(resource_type, resource_id, version)) is not None:
        raise _reject(409, "duplicate", "Recurso ya recibido (misma version)", "recurso_repetido",
                      client_id=client.client_id)

    try:
        if resource_type == "Observation":
            if patient is None:
                record = _new_record(client, resource_type, resource_id, version, resource,
                                     federated_id, None, REASON_UNKNOWN_PATIENT)
                return _queue(db, record, ip=ip, resource_type=resource_type,
                              what="Un resultado de laboratorio")
            record = _new_record(client, resource_type, resource_id, version, resource,
                                 federated_id, patient, None)
            db.add(record)
            db.flush()
            stored = finish_observation(db, record=record, patient=patient, ip=ip)
            suffix = "" if stored else " (el paciente no tiene atencion: el recurso quedo guardado)"
            return informational(201, f"Observation recibida y asociada a un paciente{suffix}. Evento {record.id}")

        # ServiceRequest
        specialist = choose_specialist(db, resource)
        if patient is None:
            record = _new_record(client, resource_type, resource_id, version, resource,
                                 federated_id, None, REASON_UNKNOWN_PATIENT)
            return _queue(db, record, ip=ip, resource_type=resource_type, what="Una remision")
        if specialist is None:
            record = _new_record(client, resource_type, resource_id, version, resource,
                                 federated_id, patient, REASON_NO_SPECIALIST)
            return _queue(db, record, ip=ip, resource_type=resource_type, what="Una remision")
        record = _new_record(client, resource_type, resource_id, version, resource,
                             federated_id, patient, None)
        db.add(record)
        db.flush()
        referral = finish_referral(db, record=record, patient=patient, specialist=specialist, ip=ip)
        return informational(201, f"ServiceRequest recibida; remision {referral.id} en la bandeja del especialista")
    except (ConflictError, IntegrityError):
        # UNIQUE (type, id, version) or the referral's idempotency key: a copy won the race.
        db.rollback()
        raise _reject(409, "duplicate", "Recurso ya recibido (misma version)", "recurso_repetido",
                      client_id=client.client_id) from None


# ---------------------------------------------------------------------------
# Entry point of the routes
# ---------------------------------------------------------------------------


async def handle(request: Request, db: Session, resource_type: str) -> JSONResponse:
    ip = client_ip(request)
    client_id: str | None = None
    try:
        client = await run_in_threadpool(
            authenticate, db, request.headers.get("authorization"), resource_type,
            request.headers.get(API_KEY_HEADER.lower()),
        )
        client_id = client.client_id
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise _reject(413, "too-long", f"El cuerpo supera {MAX_BODY_BYTES} bytes",
                          "cuerpo_demasiado_grande", client_id=client_id)
        # Streamed with a cap: a chunked body with no Content-Length is cut off at the
        # limit instead of being buffered whole.
        buffer = bytearray()
        async for chunk in request.stream():
            buffer.extend(chunk)
            if len(buffer) > MAX_BODY_BYTES:
                raise _reject(413, "too-long", f"El cuerpo supera {MAX_BODY_BYTES} bytes",
                              "cuerpo_demasiado_grande", client_id=client_id)
        raw = bytes(buffer)
        return await run_in_threadpool(
            process, db, client, request.headers, raw, resource_type, ip
        )
    except Rejection as rejection:
        await run_in_threadpool(
            _audit_rejection, db, rejection, rejection.client_id or client_id, ip, resource_type
        )
        return respond(rejection.status, rejection.issues, headers=rejection.headers)


# ---------------------------------------------------------------------------
# Admin: the reconciliation queue
# ---------------------------------------------------------------------------


def pending_stmt() -> Any:
    return (
        select(FhirInboundEvent)
        .where(FhirInboundEvent.estado_conciliacion == PENDING)
        .order_by(FhirInboundEvent.received_at, FhirInboundEvent.id)
    )


def summary_of(record: FhirInboundEvent) -> str:
    """Readable line for the queue screen (no patient data: the patient is unknown)."""

    resource = record.resource or {}
    if record.fhir_resource_type == "Bundle":
        for entry in resource.get("entry") or []:
            inner = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(inner, dict) and inner.get("resourceType") == "ServiceRequest":
                return perfil.reason_of(inner) or "Bundle de remision"
        return "Bundle"
    if record.fhir_resource_type == "Observation":
        try:
            return parse_observation(resource).describe()
        except FhirParseError:
            return "Observation"
    return perfil.reason_of(resource) or "ServiceRequest"


def locked_event_stmt(event_id: uuid.UUID) -> Any:
    return (
        select(FhirInboundEvent)
        .where(FhirInboundEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def reconcile(
    db: Session,
    *,
    event_id: uuid.UUID,
    admin: User,
    patient_id: uuid.UUID | None,
    specialist_id: uuid.UUID | None,
    ip: str | None,
) -> FhirInboundEvent:
    """Turn a pending message into the 201 path (slide 30: the 202 is later reconciled)."""

    from app.services import patients as patients_service
    from app.services.errors import NotFoundError

    # Lock the row (no-op on SQLite) so two admins reconciling the same event serialize;
    # populate_existing makes the state below the one the lock was granted on, so the
    # second admin sees "conciliado" and gets the 409 instead of a duplicate.
    record = db.scalar(locked_event_stmt(event_id))
    if record is None or record.estado_conciliacion is None:
        raise NotFoundError("Evento de interoperabilidad no encontrado")
    if record.estado_conciliacion != PENDING:
        raise ConflictError("El evento ya fue conciliado")

    target_id = patient_id or record.patient_id
    if target_id is None:
        raise ConflictError("Indique paciente_id: el mensaje no esta asociado a ningun paciente")
    patient = patients_service.get_patient(db, target_id)

    try:
        if record.fhir_resource_type == "Bundle":
            from app.services.interop.roundtrip import finish_bundle, validate_bundle

            resources = validate_bundle(record.resource, record.origen_externo or "externo")
            request = next(r for r in resources if r["resourceType"] == "ServiceRequest")
            specialist = (
                referrals_service._target_specialist(db, specialist_id)
                if specialist_id else choose_specialist(db, request)
            )
            if specialist is None:
                raise ConflictError("Indique especialista_id para conciliar el Bundle")
            finish_bundle(db, record, resources, patient, specialist, ip, admin)
            return record
        if record.fhir_resource_type == "Observation":
            finish_observation(db, record=record, patient=patient, ip=ip, reconciled_by=admin)
            return record

        if specialist_id is not None:
            specialist = referrals_service._target_specialist(db, specialist_id)  # noqa: SLF001
        else:
            specialist = choose_specialist(db, record.resource)
        if specialist is None:
            raise ConflictError("No hay especialista que reciba la remision: indique especialista_id")
        finish_referral(db, record=record, patient=patient, specialist=specialist, ip=ip,
                        reconciled_by=admin)
    except IntegrityError:
        db.rollback()
        raise ConflictError("La remision de este mensaje ya existe") from None
    return record
