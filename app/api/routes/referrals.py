"""Referrals (R08): a medico refers a patient to an especialista.

Authorization, in short:

- create: ``medico`` only, for a patient of their IPS, to an active especialista
  of any IPS. ``paciente``, ``contable``, ``coordinador``, ``admin`` and
  ``especialista`` receive 403.
- list: the especialista sees the referrals addressed to them, the medico the
  ones they sent, the admin all of them. ``paciente`` and ``contable``: 403.
- accept / reject / report: only the especialista the referral is addressed to.
  Wrong state: 409.
- Having a live (pending, accepted or completed) referral lets the especialista
  read the patient's record outside their IPS; a rejected one grants nothing.

Every state change is auto-synchronized to FHIR as a ``ServiceRequest`` in the
background (a FHIR outage never fails the request) and publishes a domain event.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Query, status

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import ReferralStatus, RoleCode
from app.models.referral import Referral
from app.schemas.referral import (
    ReferralCreate,
    ReferralRead,
    ReferralReject,
    ReferralReport,
    SpecialistRead,
)
from app.services import fhir_autosync
from app.services import referrals as referrals_service
from app.services.interop import roundtrip

router = APIRouter(prefix="/api/v1/referrals", tags=["remisiones"])

LIST_ROLES = (RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR, RoleCode.SPECIALIST)


def _answer_partner(db: DbSession, background: BackgroundTasks, referral: Referral) -> None:
    """R30: the decision AND its Task row are already committed together (``stage_answer`` ran
    inside the accept/reject transaction, so the Task is never lost). Only the delivery is left:
    it is sent after the response, so a slow partner cannot hold the specialist."""

    outbox_id = roundtrip.undelivered_answer_id(db, referral)
    if outbox_id is not None:
        background.add_task(roundtrip.deliver_queued, outbox_id)


@router.post(
    "",
    response_model=ReferralRead,
    status_code=status.HTTP_201_CREATED,
    summary="Remitir un paciente a un especialista",
)
def create_referral(
    db: DbSession, user: CurrentUser, payload: ReferralCreate, background: BackgroundTasks
) -> ReferralRead:
    authz.require_role(user, RoleCode.IPS_CLINICAL_OPERATOR)
    referral = referrals_service.create(
        db,
        actor=user,
        patient_id=payload.paciente_id,
        specialist_id=payload.especialista_id,
        motivo=payload.motivo,
    )
    fhir_autosync.enqueue(background, "referral", referral.id, user.id)
    return referrals_service.to_read(referral)


@router.get(
    "/specialists",
    response_model=list[SpecialistRead],
    summary="Especialistas activos para elegir el destino de una remision",
)
def list_specialists(db: DbSession, user: CurrentUser) -> list[dict]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    return referrals_service.list_specialists(db)


@router.get(
    "",
    response_model=list[ReferralRead],
    summary="Bandeja de remisiones (recibidas o enviadas)",
)
def list_referrals(
    db: DbSession,
    user: CurrentUser,
    rol: str | None = Query(
        default=None,
        pattern="^(recibidas|enviadas)$",
        description="recibidas (por defecto del especialista) o enviadas (por defecto del medico)",
    ),
    estado: ReferralStatus | None = Query(default=None),
) -> list[ReferralRead]:
    authz.require_role(user, *LIST_ROLES)
    return [
        referrals_service.to_read(r)
        for r in referrals_service.list_referrals(db, user, rol=rol, estado=estado)
    ]


@router.get(
    "/{referral_id}",
    response_model=ReferralRead,
    summary="Consultar una remision",
)
def get_referral(db: DbSession, user: CurrentUser, referral_id: uuid.UUID) -> ReferralRead:
    authz.require_role(user, *LIST_ROLES)
    return referrals_service.to_read(
        referrals_service.get_visible_referral(db, user, referral_id)
    )


@router.post(
    "/{referral_id}/accept",
    response_model=ReferralRead,
    summary="Aceptar la remision (solo el especialista destinatario)",
)
def accept_referral(
    db: DbSession, user: CurrentUser, referral_id: uuid.UUID, background: BackgroundTasks
) -> ReferralRead:
    authz.require_role(user, RoleCode.SPECIALIST)
    referral = referrals_service.accept(
        db, referrals_service.get_referral(db, referral_id), actor=user,
        before_commit=roundtrip.stage_answer,
    )
    fhir_autosync.enqueue(background, "referral", referral.id, user.id)
    read = referrals_service.to_read(referral)
    _answer_partner(db, background, referral)
    return read


@router.post(
    "/{referral_id}/reject",
    response_model=ReferralRead,
    summary="Rechazar la remision con un motivo (solo desde pendiente)",
)
def reject_referral(
    db: DbSession,
    user: CurrentUser,
    referral_id: uuid.UUID,
    payload: ReferralReject,
    background: BackgroundTasks,
) -> ReferralRead:
    authz.require_role(user, RoleCode.SPECIALIST)
    referral = referrals_service.reject(
        db, referrals_service.get_referral(db, referral_id), actor=user, motivo=payload.motivo,
        before_commit=roundtrip.stage_answer,
    )
    fhir_autosync.enqueue(background, "referral", referral.id, user.id)
    read = referrals_service.to_read(referral)
    _answer_partner(db, background, referral)
    return read


@router.post(
    "/{referral_id}/report",
    response_model=ReferralRead,
    summary="Entregar el informe del especialista (aceptada -> completada)",
)
def report_referral(
    db: DbSession,
    user: CurrentUser,
    referral_id: uuid.UUID,
    payload: ReferralReport,
    background: BackgroundTasks,
) -> ReferralRead:
    authz.require_role(user, RoleCode.SPECIALIST)
    referral = referrals_service.complete(
        db, referrals_service.get_referral(db, referral_id), actor=user, informe=payload.informe
    )
    fhir_autosync.enqueue(background, "referral", referral.id, user.id)
    return referrals_service.to_read(referral)
