"""Interoperability between teams (Part B, docs/INTEROP.md).

Two kinds of routes:

* **Partner routes, at the root as on slide 28** -- ``POST /oauth/token``,
  ``POST /interop/Observation`` and ``POST /interop/ServiceRequest``. They are NOT user
  endpoints: no user JWT. A partner proves itself with a system token (OAuth2 client
  credentials; or, when ``INTEROP_ALLOW_API_KEY`` is on, ``X-Api-Key``) and a per-message
  HMAC signature (optional per ``INTEROP_REQUIRE_SIGNATURE``). They sit outside ``/api/v1`` on
  purpose (decision D5); the partners reach them through ``tunnel-api``, not through the
  web's nginx.
* **Admin routes under ``/api/v1/interop``** -- the reconciliation queue and the list of
  registered partners. User JWT, ADMIN only.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.core.net import client_ip
from app.models.enums import AuditAction, RoleCode
from app.schemas.interop import (
    InteropClientRead,
    InteropDestinationRead,
    InteropPendingRead,
    InteropReconcile,
    InteropReferralCreate,
    InteropReferralRead,
)
from app.services.interop import clientes, receptor, roundtrip

# -- Partner routes (root) ------------------------------------------------------------

partner_router = APIRouter(tags=["interoperabilidad (socios)"])

MAX_TOKEN_REQUEST_BYTES = 16_384
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class TokenBodyTooLarge(Exception):
    """The token request body exceeds ``MAX_TOKEN_REQUEST_BYTES``."""


async def _read_capped(request: Request) -> bytes:
    """The body, refused (413) before reading when Content-Length is over the cap, and
    cut off while streaming when it lies or is absent (chunked)."""

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_TOKEN_REQUEST_BYTES:
        raise TokenBodyTooLarge
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > MAX_TOKEN_REQUEST_BYTES:
            raise TokenBodyTooLarge
    return bytes(buffer)


async def _token_params(request: Request) -> dict[str, str]:
    """``application/x-www-form-urlencoded`` (RFC 6749) or JSON; HTTP Basic also carries the client."""

    raw = await _read_capped(request)
    params: dict[str, str] = {}
    if "json" in request.headers.get("content-type", "").lower():
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        if isinstance(body, dict):
            params = {k: str(v) for k, v in body.items() if isinstance(v, (str, int))}
    else:
        parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        params = {key: values[0] for key, values in parsed.items() if values}

    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic" and supplied:
        try:
            user, _, secret = base64.b64decode(supplied).decode("utf-8").partition(":")
        except ValueError:
            user = secret = ""
        params.setdefault("client_id", user)
        params.setdefault("client_secret", secret)
    return params


def _token_rejection(
    db: DbSession, status: int, motivo: str, client_id: str | None, ip: str | None
) -> None:
    # Whoever fails here proved nothing yet: one audit row per (ip, reason) per minute,
    # the rest only in the app.security log.
    if not receptor.should_audit_unauthenticated(ip, motivo, status=status, client_id=client_id):
        return
    try:
        db.rollback()
        receptor.audit_event(
            db, AuditAction.INTEROP_REJECTED, client_id=client_id, ip=ip, status=status,
            resource_type="oauth/token", resultado="rechazado", motivo=motivo,
        )
        db.commit()
    except Exception:  # noqa: BLE001 -- the answer must not depend on the audit
        db.rollback()


@partner_router.post(
    "/oauth/token",
    summary="OAuth2 client credentials: token de sistema de 10 minutos",
)
async def oauth_token(request: Request, db: DbSession) -> JSONResponse:
    ip = client_ip(request)
    try:
        params = await _token_params(request)
    except TokenBodyTooLarge:
        await run_in_threadpool(_token_rejection, db, 413, "cuerpo_demasiado_grande", None, ip)
        return JSONResponse(
            status_code=413,
            content={
                "error": "invalid_request",
                "error_description": f"El cuerpo supera {MAX_TOKEN_REQUEST_BYTES} bytes",
            },
            headers=NO_STORE,
        )
    client_id = params.get("client_id") or None
    try:
        issued = await run_in_threadpool(
            clientes.issue_token,
            db,
            client_id=client_id,
            client_secret=params.get("client_secret"),
            scope=params.get("scope"),
            grant_type=params.get("grant_type"),
        )
    except clientes.TokenError as error:
        await run_in_threadpool(_token_rejection, db, error.status, error.error, client_id, ip)
        headers = dict(NO_STORE)
        if error.status == 401:
            headers["WWW-Authenticate"] = "Basic"
        return JSONResponse(
            status_code=error.status,
            content={"error": error.error, "error_description": error.description},
            headers=headers,
        )
    return JSONResponse(
        content={
            "access_token": issued.access_token,
            "token_type": "Bearer",
            "expires_in": issued.expires_in,
            "scope": issued.scope,
        },
        headers=NO_STORE,
    )


@partner_router.post(
    "/interop/Observation",
    summary="Recibir un resultado de laboratorio (Observation FHIR R4)",
)
async def receive_observation(request: Request, db: DbSession) -> JSONResponse:
    return await receptor.handle(request, db, "Observation")


@partner_router.post(
    "/interop/ServiceRequest",
    summary="Recibir una remision, o la respuesta a una enviada (ServiceRequest FHIR R4)",
)
async def receive_service_request(request: Request, db: DbSession) -> JSONResponse:
    return await receptor.handle(request, db, "ServiceRequest")


@partner_router.post(
    "/interop/Bundle",
    summary="Recibir una remision con su evidencia (Bundle FHIR R4, R30)",
)
async def receive_bundle(request: Request, db: DbSession) -> JSONResponse:
    return await receptor.handle(request, db, "Bundle")


@partner_router.post(
    "/interop/Task",
    summary="Recibir la respuesta del socio a una remision enviada (Task FHIR R4, R30)",
)
async def receive_task(request: Request, db: DbSession) -> JSONResponse:
    return await receptor.handle(request, db, "Task")


# -- Admin routes (/api/v1/interop) ---------------------------------------------------

admin_router = APIRouter(prefix="/api/v1/interop", tags=["interoperabilidad (admin)"])


def _referral_read(row: Any, client_id: str) -> InteropReferralRead:
    return InteropReferralRead(
        id=row.id,
        paciente_id=row.patient_id,
        cliente_interop_id=client_id,
        solicitud_id=row.service_request_id,
        estado=row.status,
        motivo=row.reason,
        servicio=row.service,
        prioridad=row.priority,
        resumen_respuesta=row.response_summary,
        creada_en=row.created_at,
    )


@admin_router.get(
    "/destinos",
    response_model=list[InteropDestinationRead],
    summary="Instituciones externas a las que se puede remitir (activas, con alcance de Bundle)",
)
def list_destinations(db: DbSession, user: CurrentUser) -> list[InteropDestinationRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    return [
        InteropDestinationRead(client_id=c.client_id, nombre=c.name)
        for c in clientes.list_clients(db)
        if c.is_active and clientes.SCOPE_BUNDLE in (c.scopes or [])
    ]


@admin_router.post(
    "/remisiones",
    response_model=InteropReferralRead,
    status_code=201,
    summary="Remitir un paciente a una institucion externa (solo medico, requiere consentimiento)",
)
def refer_to_partner(
    payload: InteropReferralCreate, db: DbSession, user: CurrentUser
) -> InteropReferralRead:
    authz.require_role(user, RoleCode.IPS_CLINICAL_OPERATOR)
    row = roundtrip.refer_to_partner(
        db,
        actor=user,
        patient_id=payload.paciente_id,
        client_id=payload.cliente_interop_id,
        reason=payload.motivo,
        service=payload.servicio,
        priority=payload.prioridad,
    )
    return _referral_read(row, payload.cliente_interop_id)


@admin_router.get(
    "/remisiones",
    response_model=list[InteropReferralRead],
    summary="Remisiones enviadas a instituciones externas y su respuesta",
)
def list_outbound_referrals(db: DbSession, user: CurrentUser) -> list[InteropReferralRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    return [_referral_read(row, client_id) for row, client_id in roundtrip.list_outbound(db, user)]


@admin_router.post(
    "/outbox/reintentar",
    summary="Reenviar las respuestas (Task) que no llegaron al socio (solo Admin)",
)
def retry_task_outbox(db: DbSession, user: CurrentUser) -> dict[str, int]:
    authz.require_admin(user)
    return roundtrip.retry_outbox(db)


def _pending_read(record: Any) -> InteropPendingRead:
    return InteropPendingRead(
        id=record.id,
        recurso=record.fhir_resource_type,
        cliente=record.origen_externo,
        estado=record.estado_conciliacion or "",
        motivo=record.motivo_pendiente,
        id_federado=record.id_federado_recibido,
        resumen=receptor.summary_of(record),
        paciente_id=record.patient_id,
        recibido_en=record.received_at,
        conciliado_en=record.conciliado_en,
    )


@admin_router.get(
    "/pendientes",
    response_model=list[InteropPendingRead],
    summary="Cola de conciliacion: mensajes aceptados con 202 (solo Admin)",
)
def list_pending(db: DbSession, user: CurrentUser) -> list[InteropPendingRead]:
    authz.require_admin(user)
    return [_pending_read(r) for r in db.scalars(receptor.pending_stmt().limit(200)).unique()]


@admin_router.post(
    "/pendientes/{event_id}/conciliar",
    response_model=InteropPendingRead,
    summary="Asociar un mensaje pendiente a un paciente: el 202 pasa a 201 (solo Admin)",
)
def reconcile_pending(
    event_id: uuid.UUID, payload: InteropReconcile, db: DbSession, user: CurrentUser, request: Request
) -> InteropPendingRead:
    authz.require_admin(user)
    try:
        record = receptor.reconcile(
            db,
            event_id=event_id,
            admin=user,
            patient_id=payload.paciente_id,
            specialist_id=payload.especialista_id,
            ip=client_ip(request),
        )
    except receptor.Rejection as rejection:
        # A queued Bundle is validated again when reconciled; the rules may have changed
        # since it was queued. Nothing was written: tell the admin why, do not answer 500.
        db.rollback()
        reasons = "; ".join(issue.diagnostics for issue in rejection.issues) or rejection.motivo
        raise HTTPException(
            status_code=422,
            detail=f"El mensaje pendiente ya no cumple el perfil y no se puede conciliar: {reasons}",
        ) from None
    return _pending_read(record)


@admin_router.get(
    "/clientes",
    response_model=list[InteropClientRead],
    summary="Socios registrados (sin secretos; solo Admin)",
)
def list_clients(db: DbSession, user: CurrentUser) -> list[InteropClientRead]:
    authz.require_admin(user)
    return [
        InteropClientRead(
            client_id=c.client_id,
            nombre=c.name,
            alcances=list(c.scopes),
            activo=c.is_active,
            creado_en=c.created_at,
            ultimo_uso=c.last_used_at,
        )
        for c in clientes.list_clients(db)
    ]
