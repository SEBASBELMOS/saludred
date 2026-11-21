"""Request-level logic of the agent endpoints (sync, runs in a worker thread)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core import authz
from app.models.enums import AuditAction
from app.models.identity import User
from app.services import patient_access, pseudonymization, trail
from app.services import patients as patients_service
from app.services.agent.context import REFERRING_ROLE, AgentContext
from app.services.errors import commit
from app.services.rag import privacy

ENTITY = "agent"


def contract_role(user: User) -> str:
    return authz.CONTRACT_ROLE_NAMES[user.role.code]


def open_turn(
    db: Session, user: User, patient_id: uuid.UUID | None, message: str, client_ip: str | None = None
) -> tuple[AgentContext, str]:
    """Authorize, fix the patient and scrub the clinician's message.

    The returned message is the ONLY text of the clinician that can reach the
    model: whatever name, document, phone or e-mail they typed is replaced first.
    """

    authz.require_role(user, *authz.AI_REVIEWERS)
    alias = None
    if patient_id is not None:
        patient = patients_service.get_patient(db, patient_id)
        patient_access.ensure_staff_patient_scope(db, user, patient.id)
        alias = pseudonymization.pseudonym_for(patient.id)
        safe = privacy.scrub(message, privacy.PatientIdentity.of(patient, alias))
    else:
        safe = privacy.scrub_generic(message)
    ctx = AgentContext(
        user_id=user.id,
        username=user.username,
        role=contract_role(user),
        patient_id=patient_id,
        patient_alias=alias,
        client_ip=client_ip,
    )
    return ctx, safe


def user_context(user: User, client_ip: str | None = None) -> AgentContext:
    authz.require_role(user, *authz.AI_REVIEWERS)
    return AgentContext(
        user_id=user.id, username=user.username, role=contract_role(user), client_ip=client_ip
    )


def describe_actions(
    db: Session, actions: list[dict[str, Any]], *, debug: bool = False
) -> list[dict[str, Any]]:
    """What waits for the clinician, in words: no tool names, no ids.

    A referral shows the specialist's name and IPS and the proposed reason; a
    preference shows its text. In test mode (``AGENT_DEBUG``) the raw tool name and
    arguments are added.
    """

    from app.services import referrals as referrals_service

    names = {str(s["id"]): s for s in referrals_service.list_specialists(db)}
    described = []
    for action in actions:
        args = action.get("args", {})
        item: dict[str, Any]
        if action.get("herramienta") == "proponer_remision":
            item = {"accion": "remision", "motivo": str(args.get("motivo", ""))[:900]}
            specialist = names.get(str(args.get("especialista_id")))
            if specialist is not None:
                item["especialista"] = specialist["nombre"]
                item["organizacion"] = specialist["organizacion"]
        elif action.get("herramienta") == "guardar_preferencia":
            item = {"accion": "preferencia", "texto": str(args.get("preferencia", ""))[:200]}
        else:
            item = {"accion": "otra"}
        if debug:
            item["herramienta"] = action.get("herramienta")
            item["args"] = args
        described.append(item)
    return described


def start_messages(
    db: Session, user: User, patient_id: uuid.UUID | None
) -> tuple[list[str], list[str]]:
    """The predefined first messages of a conversation. No model call, no tokens.

    The greeting comes from the authenticated session (name, role) and the open
    patient shows only the pseudonym: identifying the user is code, not the model.
    """

    authz.require_role(user, *authz.AI_REVIEWERS)
    role = contract_role(user)
    messages = [f"Hola, {user.full_name}. Soy el asistente clínico de SaludRed ({role})."]
    if patient_id is not None:
        patient = patients_service.get_patient(db, patient_id)
        patient_access.ensure_staff_patient_scope(db, user, patient.id)
        messages.append(f"Paciente abierto: {pseudonymization.pseudonym_for(patient.id)}.")
    else:
        messages.append("No hay un paciente abierto; ábralo si su pregunta es sobre un caso.")
    messages.append(
        "Puedo apoyarle con criterios de triage, criticidad, camas y remisiones. "
        "No reemplazo su juicio clínico: propongo y usted decide."
    )
    shortcuts = ["¿Qué criterios de triage aplican a este caso?", "Resume la criticidad de este paciente."]
    if role == REFERRING_ROLE:
        shortcuts.append("Propón una remisión para este paciente.")
    return messages, shortcuts


def audit(
    db: Session,
    user: User,
    action: AuditAction,
    *,
    patient_id: uuid.UUID | None,
    conversation: str,
    extra: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """One audit entry for a turn. It records WHAT happened, never the text."""

    trail.audit(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=None,
        actor=user,
        metadata={
            "patient_id": str(patient_id) if patient_id else None,
            "pseudonym": pseudonymization.pseudonym_for(patient_id) if patient_id else None,
            "conversation": conversation,
            **(extra or {}),
        },
        ip_address=ip,
    )
    commit(db)
