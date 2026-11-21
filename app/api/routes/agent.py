"""Clinical agent endpoints (docs/AGENTE.md).

Authorization, in short:

- chat / decidir / inicio: ``medico`` or ``especialista`` only (403 for everybody else).
- With ``paciente_id`` the patient must belong to the user's IPS (or be referred to
  the specialist); the model never receives the id, the name or the document.
- A write proposed by the agent (a referral, a stored preference) stays pending until
  the same clinician approves, rejects or cancels it with ``/agente/decidir``. Only a
  ``medico`` can propose a referral.
- A conversation is bound to the first patient it was opened with (409 otherwise).
- The request's database connection is returned to the pool BEFORE the model is
  awaited (a turn can last 90 s); the audit afterwards uses a fresh short transaction.
- After repeated injection or off-topic abuse the calling USER (not the address, which
  is shared behind nginx / the tunnel; the IP stays in the audit row) is blocked for a while
  on these routes (429 with ``Retry-After``); every security event is logged and audited.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.core.config import get_settings
from app.core.net import client_ip
from app.models.enums import AuditAction
from app.schemas.agent import AgentDecision, AgentMessage, AgentReply, AgentStart
from app.services.agent import guard, runtime, security
from app.services.agent import service as agent_service
from app.services.agent.flow import ROUTE_AGENT, ROUTE_OUT

router = APIRouter(prefix="/api/v1/agente", tags=["Agente clinico"])

BUSY_RETRY_SECONDS = 5


def _refuse_if_blocked(ip: str | None, user: object) -> None:
    left = security.blocklist.remaining(security.block_key(user, ip))
    if left:
        security.log_event(
            kind=security.BLOCKED_REQUEST, level="alto", source="agente",
            username=getattr(user, "username", None), ip=ip, action="rechazada_429",
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Acceso al agente bloqueado temporalmente por actividad sospechosa",
            headers={"Retry-After": str(left)},
        )


def _busy() -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "El asistente tiene demasiadas consultas en curso; vuelva a intentarlo en unos segundos",
        headers={"Retry-After": str(BUSY_RETRY_SECONDS)},
    )


def _public(result: dict, route: str | None) -> dict:
    """The clinician's view: ``ruta`` and ``motivo`` are technical, test mode only."""

    shown = {k: v for k, v in result.items() if k not in {"motivo", "ruta"}}
    if "respuesta" in shown:
        # R23: an answer always carries ``fuentes``; empty means "no support" or a fixed reply.
        shown.setdefault("fuentes", [])
    if get_settings().agent_debug:
        shown["ruta"] = route
        if "motivo" in result:
            shown["motivo"] = result["motivo"]
    return shown


async def _release(db: Session) -> None:
    """Return the request's connection to the pool before the long await."""

    await run_in_threadpool(db.commit)


@router.post(
    "/chat",
    response_model=AgentReply,
    response_model_exclude_none=True,
    summary="Conversar con el agente clinico (herramientas, citas y aprobacion humana)",
)
async def chat(request: Request, db: DbSession, user: CurrentUser, payload: AgentMessage) -> dict:
    authz.require_role(user, *authz.AI_REVIEWERS)
    ip = client_ip(request)
    _refuse_if_blocked(ip, user)
    ctx, safe_message = await run_in_threadpool(
        agent_service.open_turn, db, user, payload.paciente_id, payload.mensaje, ip
    )

    # Layer 2 of the guard: an alarm, not a wall. The turn continues; the strike
    # may block the NEXT requests of this address.
    alert: dict = {}
    detection = guard.scan(payload.mensaje)
    if detection.detected:
        await run_in_threadpool(
            security.record_event, db, user,
            kind=security.INJECTION_MESSAGE, level=detection.level, source="mensaje",
            action="registrado", ip=ip, patterns=detection.patterns,
            patient_id=payload.paciente_id, conversation=payload.conversacion_id,
        )
        alert = {"alerta": detection.level}
        if detection.level == guard.HIGH and await run_in_threadpool(
            security.strike_and_maybe_block, db, user,
            reason="inyeccion_detectada", ip=ip, conversation=payload.conversacion_id,
        ):
            alert["bloqueo"] = True
    await _release(db)

    state = await runtime.get_flow().ainvoke(
        {"message": safe_message, "conversation": payload.conversacion_id, "ctx": ctx}
    )
    route, result = state["route"], dict(state["result"])
    if result["estado"] == "conversacion_de_otro_paciente":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esta conversacion pertenece a otro paciente: inicie una conversacion nueva",
        )
    if result["estado"] == "ocupado":
        raise _busy()
    if route == ROUTE_OUT:
        await run_in_threadpool(
            security.record_event, db, user,
            kind=security.OFF_TOPIC, level=guard.MEDIUM, source="mensaje",
            action="respuesta_fija", ip=ip, patient_id=payload.paciente_id,
            conversation=payload.conversacion_id,
        )
        if await run_in_threadpool(
            security.strike_and_maybe_block, db, user,
            reason="desvio_reiterado", ip=ip, conversation=payload.conversacion_id,
        ):
            alert["bloqueo"] = True
    if result["estado"] == "pendiente_aprobacion" and result.get("acciones"):
        result["acciones"] = await run_in_threadpool(
            agent_service.describe_actions, db, result["acciones"], debug=get_settings().agent_debug
        )
    await run_in_threadpool(
        agent_service.audit,
        db,
        user,
        AuditAction.AGENT_CHAT,
        patient_id=payload.paciente_id,
        conversation=payload.conversacion_id,
        extra={
            "estado": result["estado"],
            "ruta": route,
            "motivo": result.get("motivo"),
            "message_chars": len(payload.mensaje),
            # A flagged message is not copied into the audit: the security event
            # already holds the matched patterns, and the attack text stays out.
            "payload_llm": {
                "enviado_al_llm": (
                    "[omitido: alerta de seguridad]" if detection.detected else safe_message[:3000]
                ),
                "truncado": not detection.detected and len(safe_message) > 3000,
            },
            **alert,
        },
        ip=ip,
    )
    return _public(result, route)


@router.post(
    "/inicio",
    response_model=AgentReply,
    response_model_exclude_none=True,
    summary="Mensajes iniciales predefinidos de una conversacion (sin llamar al modelo)",
)
async def start(request: Request, db: DbSession, user: CurrentUser, payload: AgentStart) -> dict:
    authz.require_role(user, *authz.AI_REVIEWERS)
    ip = client_ip(request)
    _refuse_if_blocked(ip, user)
    messages, shortcuts = await run_in_threadpool(
        agent_service.start_messages, db, user, payload.paciente_id
    )
    await run_in_threadpool(
        agent_service.audit,
        db,
        user,
        AuditAction.AGENT_CHAT,
        patient_id=payload.paciente_id,
        conversation=payload.conversacion_id,
        extra={"estado": "inicio", "ruta": "inicio", "message_chars": 0},
        ip=ip,
    )
    return _public(
        {
            "estado": "inicio",
            "conversacion": payload.conversacion_id,
            "mensajes": messages,
            "atajos": shortcuts,
        },
        "inicio",
    )


@router.post(
    "/decidir",
    response_model=AgentReply,
    response_model_exclude_none=True,
    summary="Aprobar, rechazar o cancelar la accion que el agente dejo pendiente",
)
async def decide(request: Request, db: DbSession, user: CurrentUser, payload: AgentDecision) -> dict:
    authz.require_role(user, *authz.AI_REVIEWERS)
    ip = client_ip(request)
    _refuse_if_blocked(ip, user)
    ctx = await run_in_threadpool(agent_service.user_context, user, ip)
    await _release(db)
    harness = runtime.get_harness()
    result = await harness.decide(ctx, payload.conversacion_id, payload.decision, payload.motivo)
    if result["estado"] == "sin_pendientes":
        raise HTTPException(status.HTTP_409_CONFLICT, "No hay ninguna accion pendiente en esa conversacion")
    if result["estado"] == "no_autorizado":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el personal clinico decide sobre el agente")
    if result["estado"] == "ocupado":
        raise _busy()
    result = dict(result)
    if result["estado"] == "pendiente_aprobacion" and result.get("acciones"):
        result["acciones"] = await run_in_threadpool(
            agent_service.describe_actions, db, result["acciones"], debug=get_settings().agent_debug
        )
    patient_id = await harness.patient_of(user.id, payload.conversacion_id)
    await run_in_threadpool(
        agent_service.audit,
        db,
        user,
        AuditAction.AGENT_DECISION,
        patient_id=patient_id,
        conversation=payload.conversacion_id,
        extra={"decision": payload.decision, "estado": result["estado"]},
        ip=ip,
    )
    return _public(result, ROUTE_AGENT)
