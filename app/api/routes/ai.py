"""AI analysis and report endpoints (R10, R12).

Autorizacion, en resumen:

- Pedir un analisis, aprobar o rechazar un reporte: solo medico o especialista,
  y solo para pacientes atendidos en su IPS. Administrador, coordinador,
  paciente y contable reciben 403 (la IA propone, un clinico decide).
- Consultar reportes y el payload enviado: roles institucionales, dentro de su
  alcance. Paciente y contable reciben 403.
- No existe DELETE: un reporte rechazado queda como evidencia.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import AiReportStatus
from app.schemas.ai import (
    AiAnalysisCreate,
    AiPayloadRead,
    AiReportApprove,
    AiReportRead,
    AiReportReject,
)
from app.services import ai_reports, ml_client, patient_access
from app.services import patients as patients_service

router = APIRouter(prefix="/api/v1", tags=["inteligencia artificial"])


def _report_in_scope(db: DbSession, user: CurrentUser, report_id: uuid.UUID):  # noqa: ANN202
    report = ai_reports.get_report(db, report_id)
    patient_access.ensure_staff_patient_scope(db, user, report.patient_id)
    return report


@router.post(
    "/patients/{patient_id}/ai-analyses",
    response_model=AiReportRead,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar un analisis de IA (crea un reporte pendiente)",
)
def request_analysis(
    db: DbSession,
    user: CurrentUser,
    patient_id: uuid.UUID,
    payload: AiAnalysisCreate | None = None,
) -> AiReportRead:
    """Arma el payload minimizado y seudonimizado, consulta el modelo y deja un
    reporte `pendiente` de origen `manual`. Si el servicio de IA no responde: 503
    y no se guarda nada.

    El analisis normal es automatico (ver docs/IA.md): esta ruta se conserva para
    el contrato. Es tolerante a repeticiones: si ya hay un reporte `pendiente` del
    paciente hecho con un payload identico al que se enviaria ahora, se devuelve
    ese mismo reporte (mismo codigo y forma) en lugar de crear un duplicado."""

    authz.require_role(user, *authz.AI_REVIEWERS)
    patient = patients_service.get_patient(db, patient_id)
    patient_access.ensure_staff_patient_scope(db, user, patient.id)
    report = ai_reports.request_analysis(
        db, patient, actor=user, triage_level=payload.triage_level if payload else None
    )
    return ai_reports.to_read(report)


@router.get(
    "/ai-reports",
    response_model=list[AiReportRead],
    summary="Listar reportes de IA (bandeja de revision)",
)
def list_reports(
    db: DbSession,
    user: CurrentUser,
    estado: AiReportStatus | None = Query(default=None),
    patient_id: uuid.UUID | None = Query(default=None),
) -> list[AiReportRead]:
    authz.require_staff(user)
    return [
        ai_reports.to_read(r)
        for r in ai_reports.list_reports(db, user, status=estado, patient_id=patient_id)
    ]


@router.get(
    "/ai-reports/{report_id}",
    response_model=AiReportRead,
    summary="Consultar un reporte de IA",
)
def get_report(db: DbSession, user: CurrentUser, report_id: uuid.UUID) -> AiReportRead:
    authz.require_staff(user)
    return ai_reports.to_read(_report_in_scope(db, user, report_id))


@router.get(
    "/ai-reports/{report_id}/payload",
    response_model=AiPayloadRead,
    summary="JSON exacto enviado al modelo",
)
def get_payload(db: DbSession, user: CurrentUser, report_id: uuid.UUID) -> AiPayloadRead:
    authz.require_staff(user)
    report = _report_in_scope(db, user, report_id)
    return AiPayloadRead(payload=report.payload_sent)


@router.post(
    "/ai-reports/{report_id}/approve",
    response_model=AiReportRead,
    summary="Aprobar un reporte (firma clinica)",
)
def approve_report(
    db: DbSession,
    user: CurrentUser,
    report_id: uuid.UUID,
    payload: AiReportApprove | None = None,
) -> AiReportRead:
    """Solo desde `pendiente` (409 si ya fue revisado). Al aprobar, la
    probabilidad pasa a ser el puntaje de criticidad de la solicitud de cama
    abierta del paciente, si la hay."""

    authz.require_role(user, *authz.AI_REVIEWERS)
    report = _report_in_scope(db, user, report_id)
    report = ai_reports.approve(
        db, report, actor=user, observaciones=payload.observaciones if payload else None
    )
    return ai_reports.to_read(report)


@router.post(
    "/ai-reports/{report_id}/reject",
    response_model=AiReportRead,
    summary="Rechazar un reporte (queda como evidencia)",
)
def reject_report(
    db: DbSession, user: CurrentUser, report_id: uuid.UUID, payload: AiReportReject
) -> AiReportRead:
    authz.require_role(user, *authz.AI_REVIEWERS)
    report = _report_in_scope(db, user, report_id)
    report = ai_reports.reject(
        db, report, actor=user, motivo=payload.motivo, observaciones=payload.observaciones
    )
    return ai_reports.to_read(report)


@router.get(
    "/ai/models",
    summary="Metricas, umbrales y sesgo por subgrupo de los modelos",
)
def ai_models(user: CurrentUser) -> dict[str, Any]:
    """Respuesta directa del servicio de IA. 503 si no esta disponible."""

    authz.require_staff(user)
    return ml_client.get_client().models()
