"""Referrals between clinicians (R08).

Life cycle: ``pendiente`` -> ``aceptada`` -> ``completada``; the specialist may
also answer ``rechazada`` while the referral is pending. Anything else is a 409.

* **Create:** only a ``medico``, for a patient of their IPS, towards an active
  ``especialista`` of ANY IPS of the network (referring across IPS is the point
  of an EPS network). Duplicates are allowed: a second referral of the same
  patient to the same specialist is a new clinical request.
* **Act:** only the specialist the referral is addressed to. Not the sender,
  not another specialist, not the admin.
* **Access:** a pending, accepted or completed referral lets the specialist read
  the patient's record (``patient_access``); a rejected one grants nothing.

Nothing here notifies or bills. Each transition commits, audits and publishes a
domain event (``remision_creada`` ...); the reactors do the rest (inbox, live
channel, invoice, counters). See ``docs/EVENTOS.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.events.bus import DomainEvent, publish
from app.models.enums import AuditAction, ReferralStatus, RoleCode
from app.models.identity import Role, User
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.referral import Referral
from app.schemas.referral import ReferralRead
from app.services import patient_access, trail
from app.services import patients as patients_service
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
    commit,
)

ENTITY = "referrals"
EVENT_CREATED = "remision_creada"
EVENT_ACCEPTED = "remision_aceptada"
EVENT_REJECTED = "remision_rechazada"
EVENT_COMPLETED = "remision_completada"

MAX_LIST = 200
ROL_RECEIVED = "recibidas"
ROL_SENT = "enviadas"
MAX_MESSAGE = 480  # notifications.message is 500 characters


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(text: str, limit: int = MAX_MESSAGE) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "..."


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def specialists_stmt() -> Select[tuple[User, Organization | None]]:
    """Active, unlocked especialistas with their IPS, for the picker."""

    return (
        select(User, Organization)
        .join(Role, Role.id == User.role_id)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(
            Role.code == RoleCode.SPECIALIST,
            User.is_active.is_(True),
            User.locked_at.is_(None),
        )
        .order_by(Organization.name, User.full_name, User.id)
    )


def list_specialists(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": user.id,
            "nombre": user.full_name,
            "organizacion": organization.name if organization is not None else None,
        }
        for user, organization in db.execute(specialists_stmt())
    ]


def referrals_stmt(
    user: User, *, rol: str | None = None, estado: ReferralStatus | None = None
) -> Select[tuple[Referral]]:
    """Referrals the caller may list: sent, received, or (admin) all.

    The specialist defaults to the received ones and the medico to the sent
    ones; ``rol`` picks the other side (which is simply empty for them). The
    admin sees every referral whatever ``rol`` says. A soft-deleted patient's
    referrals leave the list.
    """

    role = user.role.code
    stmt = (
        select(Referral)
        .options(
            joinedload(Referral.patient),
            joinedload(Referral.referring_user),
            joinedload(Referral.specialist_user).joinedload(User.organization),
        )
        .join(Patient, Patient.id == Referral.patient_id)
        .where(Patient.deleted_at.is_(None))
    )
    if role != RoleCode.ADMIN:
        chosen = rol or (ROL_RECEIVED if role == RoleCode.SPECIALIST else ROL_SENT)
        column = (
            Referral.specialist_user_id if chosen == ROL_RECEIVED else Referral.referring_user_id
        )
        stmt = stmt.where(column == user.id)
    if estado is not None:
        stmt = stmt.where(Referral.status == estado)
    return stmt


def list_referrals(
    db: Session, user: User, *, rol: str | None = None, estado: ReferralStatus | None = None
) -> list[Referral]:
    stmt = referrals_stmt(user, rol=rol, estado=estado)
    return list(
        db.scalars(
            stmt.order_by(Referral.created_at.desc(), Referral.id).limit(MAX_LIST)
        ).unique()
    )


def get_referral(db: Session, referral_id: uuid.UUID) -> Referral:
    referral = db.scalar(
        select(Referral)
        .where(Referral.id == referral_id)
        .execution_options(populate_existing=True)
    )
    if referral is None:
        raise NotFoundError("Remision no encontrada")
    return referral


def get_visible_referral(db: Session, user: User, referral_id: uuid.UUID) -> Referral:
    """Admin, the sender or the addressee; anybody else gets a 404."""

    referral = get_referral(db, referral_id)
    if user.role.code != RoleCode.ADMIN and user.id not in (
        referral.referring_user_id,
        referral.specialist_user_id,
    ):
        raise NotFoundError("Remision no encontrada")
    return referral


def to_read(referral: Referral) -> ReferralRead:
    patient = referral.patient
    specialist = referral.specialist_user
    organization = specialist.organization
    return ReferralRead(
        id=referral.id,
        paciente_id=referral.patient_id,
        paciente_nombre=f"{patient.first_name} {patient.last_name}",
        motivo=referral.motivo,
        estado=referral.status,
        remitente_id=referral.referring_user_id,
        remitente_nombre=referral.referring_user.full_name,
        especialista_id=referral.specialist_user_id,
        especialista_nombre=specialist.full_name,
        especialista_organizacion=organization.name if organization is not None else None,
        motivo_rechazo=referral.motivo_rechazo,
        informe=referral.informe_especialista,
        creada_en=referral.created_at,
        aceptada_en=referral.accepted_at,
        rechazada_en=referral.rejected_at,
        completada_en=referral.completed_at,
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _audit_meta(referral: Referral, **extra: object) -> dict[str, Any]:
    return {
        "referral_id": str(referral.id),
        "patient_id": str(referral.patient_id),
        "referring_user_id": str(referral.referring_user_id),
        "specialist_user_id": str(referral.specialist_user_id),
        "estado": referral.status.value,
        **extra,
    }


def _publish(
    db: Session,
    referral: Referral,
    tipo: str,
    mensaje: str,
    to: uuid.UUID,
    **extra: object,
) -> None:
    """After the commit: the reactors notify, bill and move the counters."""

    publish(
        DomainEvent(
            tipo=tipo,
            mensaje=_clip(mensaje),
            recipients=(to,),
            paciente_id=referral.patient_id,
            datos={
                "remision_id": str(referral.id),
                "estado": referral.status.value,
                "remitente_id": str(referral.referring_user_id),
                "especialista_id": str(referral.specialist_user_id),
                **extra,
            },
        ),
        db,
    )


def _patient_name(referral: Referral) -> str:
    return f"{referral.patient.first_name} {referral.patient.last_name}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _target_specialist(db: Session, specialist_id: uuid.UUID) -> User:
    specialist = db.scalar(
        select(User)
        .options(joinedload(User.role), joinedload(User.organization))
        .where(User.id == specialist_id)
    )
    if specialist is None:
        raise NotFoundError("Especialista no encontrado")
    if (
        specialist.role.code != RoleCode.SPECIALIST
        or not specialist.is_active
        or specialist.is_locked
    ):
        raise InvalidValueError("El destino de una remision debe ser un especialista activo")
    return specialist


def create(
    db: Session, *, actor: User, patient_id: uuid.UUID, specialist_id: uuid.UUID, motivo: str
) -> Referral:
    reason = (motivo or "").strip()
    if not reason:
        raise InvalidValueError("La remision exige un motivo")
    patient = patients_service.get_patient(db, patient_id)
    patient_access.ensure_staff_patient_scope(db, actor, patient.id)
    specialist = _target_specialist(db, specialist_id)

    referral = Referral(
        patient_id=patient.id,
        referring_user_id=actor.id,
        specialist_user_id=specialist.id,
        motivo=reason,
        status=ReferralStatus.PENDING,
        created_at=_now(),
    )
    db.add(referral)
    db.flush()
    trail.audit(
        db,
        action=AuditAction.REFERRAL_CREATED,
        entity_type=ENTITY,
        entity_id=referral.id,
        actor=actor,
        metadata=_audit_meta(referral, motivo=reason),
    )
    commit(db)
    referral = get_referral(db, referral.id)
    _publish(
        db,
        referral,
        EVENT_CREATED,
        f"Nueva remision de {actor.full_name}: {_patient_name(referral)}. Motivo: {reason}",
        specialist.id,
    )
    return referral


# ---------------------------------------------------------------------------
# Specialist actions
# ---------------------------------------------------------------------------


def _ensure_addressee(referral: Referral, actor: User) -> None:
    if referral.specialist_user_id != actor.id:
        raise ForbiddenError("Solo el especialista destinatario puede responder esta remision")


def _ensure_state(referral: Referral, expected: ReferralStatus, action: str) -> None:
    if referral.status != expected:
        raise ConflictError(
            f"No se puede {action} una remision en estado {referral.status.value}"
        )


def accept(db: Session, referral: Referral, *, actor: User) -> Referral:
    _ensure_addressee(referral, actor)
    _ensure_state(referral, ReferralStatus.PENDING, "aceptar")
    referral.status = ReferralStatus.ACCEPTED
    referral.accepted_at = _now()
    trail.audit(
        db,
        action=AuditAction.REFERRAL_ACCEPTED,
        entity_type=ENTITY,
        entity_id=referral.id,
        actor=actor,
        metadata=_audit_meta(referral),
    )
    commit(db)
    referral = get_referral(db, referral.id)
    _publish(
        db,
        referral,
        EVENT_ACCEPTED,
        f"{actor.full_name} acepto la remision de {_patient_name(referral)}",
        referral.referring_user_id,
        # The billing reactor invoices the IPS that provides the consultation.
        ips_id=str(actor.organization_id) if actor.organization_id else None,
    )
    return referral


def reject(db: Session, referral: Referral, *, actor: User, motivo: str) -> Referral:
    _ensure_addressee(referral, actor)
    _ensure_state(referral, ReferralStatus.PENDING, "rechazar")
    reason = (motivo or "").strip()
    if not reason:
        raise InvalidValueError("Rechazar una remision exige indicar el motivo")
    referral.status = ReferralStatus.REJECTED
    referral.rejected_at = _now()
    referral.motivo_rechazo = reason
    trail.audit(
        db,
        action=AuditAction.REFERRAL_REJECTED,
        entity_type=ENTITY,
        entity_id=referral.id,
        actor=actor,
        metadata=_audit_meta(referral, motivo_rechazo=reason),
    )
    commit(db)
    referral = get_referral(db, referral.id)
    _publish(
        db,
        referral,
        EVENT_REJECTED,
        f"{actor.full_name} rechazo la remision de {_patient_name(referral)}: {reason}",
        referral.referring_user_id,
    )
    return referral


def complete(db: Session, referral: Referral, *, actor: User, informe: str) -> Referral:
    _ensure_addressee(referral, actor)
    _ensure_state(referral, ReferralStatus.ACCEPTED, "completar")
    report = (informe or "").strip()
    if not report:
        raise InvalidValueError("El informe del especialista no puede estar vacio")
    referral.status = ReferralStatus.COMPLETED
    referral.completed_at = _now()
    referral.informe_especialista = report
    trail.audit(
        db,
        action=AuditAction.REFERRAL_COMPLETED,
        entity_type=ENTITY,
        entity_id=referral.id,
        actor=actor,
        metadata=_audit_meta(referral),
    )
    commit(db)
    referral = get_referral(db, referral.id)
    _publish(
        db,
        referral,
        EVENT_COMPLETED,
        f"{actor.full_name} completo la remision de {_patient_name(referral)}: "
        "el informe del especialista esta disponible",
        referral.referring_user_id,
    )
    return referral
