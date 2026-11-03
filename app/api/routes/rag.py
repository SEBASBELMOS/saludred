"""Clinical RAG endpoints.

Autorizacion, en resumen:

- Preguntar sin paciente: cualquier rol institucional (administrador,
  coordinador, medico, especialista).
- Preguntar sobre un paciente (usa su historia seudonimizada y deja una
  sugerencia pendiente): solo medico o especialista de la IPS que lo atiende.
- Aprobar o rechazar una sugerencia: solo medico o especialista, y 409 si ya fue
  revisada. Al aprobar entra a la historia clinica y alimenta futuras consultas.
- Consultar sugerencias y estadisticas: roles institucionales, dentro de su alcance.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import RagSuggestionStatus
from app.schemas.rag import (
    RagAnswer,
    RagAsk,
    RagStats,
    RagSuggestionApprove,
    RagSuggestionDetail,
    RagSuggestionRead,
    RagSuggestionReject,
)
from app.services import patient_access, rag_suggestions
from app.services.rag.stats import build_stats

router = APIRouter(prefix="/api/v1", tags=["RAG clinico"])


def _in_scope(db: DbSession, user: CurrentUser, suggestion_id: uuid.UUID):  # noqa: ANN202
    suggestion = rag_suggestions.get_suggestion(db, suggestion_id)
    patient_access.ensure_staff_patient_scope(db, user, suggestion.patient_id)
    return suggestion


@router.post(
    "/rag/ask",
    response_model=RagAnswer,
    summary="Preguntar al asistente clinico (RAG con fuentes citadas)",
)
def ask(db: DbSession, user: CurrentUser, payload: RagAsk) -> RagAnswer:
    """Responde solo con las fuentes del sistema y las cita como [n]. Si la evidencia
    no alcanza, responde con la abstencion y no guarda nada. Con `patient_id` se
    agrega la historia seudonimizada del paciente y queda una sugerencia
    `pendiente` de revision clinica."""

    result = rag_suggestions.ask(
        db,
        user,
        question=payload.question,
        patient_id=payload.patient_id,
        base=payload.base,
        method=payload.method,
        k=payload.k,
    )
    return RagAnswer(
        answer=result.answer,
        mode=result.mode,
        sources=result.sources,
        suggestion_id=result.suggestion_id,
        retrieval_degraded=result.retrieval_degraded,
        degraded_reason=result.degraded_reason,
    )


@router.get(
    "/rag/suggestions",
    response_model=list[RagSuggestionRead],
    summary="Listar sugerencias del asistente (bandeja de revision)",
)
def list_suggestions(
    db: DbSession,
    user: CurrentUser,
    estado: RagSuggestionStatus | None = Query(default=None),
    patient_id: uuid.UUID | None = Query(default=None),
) -> list[RagSuggestionRead]:
    authz.require_staff(user)
    return [
        rag_suggestions.to_read(s)
        for s in rag_suggestions.list_suggestions(db, user, status=estado, patient_id=patient_id)
    ]


@router.get(
    "/rag/suggestions/{suggestion_id}",
    response_model=RagSuggestionDetail,
    summary="Consultar una sugerencia con el prompt exacto enviado al modelo",
)
def get_suggestion(
    db: DbSession, user: CurrentUser, suggestion_id: uuid.UUID
) -> RagSuggestionDetail:
    authz.require_staff(user)
    return rag_suggestions.to_detail(_in_scope(db, user, suggestion_id))


@router.post(
    "/rag/suggestions/{suggestion_id}/approve",
    response_model=RagSuggestionRead,
    summary="Aprobar una sugerencia (firma clinica)",
)
def approve_suggestion(
    db: DbSession,
    user: CurrentUser,
    suggestion_id: uuid.UUID,
    payload: RagSuggestionApprove | None = None,
) -> RagSuggestionRead:
    """Solo desde `pendiente` (409 si ya fue revisada). Al aprobar entra a la historia
    clinica del paciente y se indexa como conocimiento de ese paciente."""

    authz.require_role(user, *authz.AI_REVIEWERS)
    suggestion = _in_scope(db, user, suggestion_id)
    suggestion = rag_suggestions.approve(
        db, suggestion, actor=user, observaciones=payload.observaciones if payload else None
    )
    return rag_suggestions.to_read(suggestion)


@router.post(
    "/rag/suggestions/{suggestion_id}/reject",
    response_model=RagSuggestionRead,
    summary="Rechazar una sugerencia (queda como evidencia)",
)
def reject_suggestion(
    db: DbSession, user: CurrentUser, suggestion_id: uuid.UUID, payload: RagSuggestionReject
) -> RagSuggestionRead:
    authz.require_role(user, *authz.AI_REVIEWERS)
    suggestion = _in_scope(db, user, suggestion_id)
    suggestion = rag_suggestions.reject(
        db, suggestion, actor=user, motivo=payload.motivo, observaciones=payload.observaciones
    )
    return rag_suggestions.to_read(suggestion)


@router.get(
    "/rag/stats",
    response_model=RagStats,
    summary="Fragmentos indexados y configuracion del asistente",
)
def stats(db: DbSession, user: CurrentUser) -> RagStats:
    authz.require_staff(user)
    return build_stats(db)
