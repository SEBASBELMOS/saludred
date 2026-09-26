"""Account administration: locking, unlocking and listing users."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.security import hash_password
from app.models.enums import AuditAction, OrganizationType, RoleCode
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.identity import Role
from app.schemas.auth import AccountCreate
from app.models.identity import User
from app.services import trail
from app.services.errors import ConflictError, InvalidValueError, NotFoundError, commit


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.scalar(
        select(User).options(joinedload(User.role)).where(User.id == user_id)
    )
    if user is None:
        raise NotFoundError("Usuario no encontrado")
    return user


def list_users(
    db: Session, *, page: int, page_size: int, only_locked: bool = False
) -> tuple[list[User], int]:
    stmt = select(User).options(joinedload(User.role))
    if only_locked:
        stmt = stmt.where(User.locked_at.is_not(None))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(User.username)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def unlock_user(db: Session, user: User, *, actor: User, reason: str | None) -> User:
    """Release a locked account.

    Al desbloquear se pone el contador en cero. Dejarlo en el limite haria que
    el proximo error tipografico volviera a bloquear la cuenta de inmediato, y
    el desbloqueo no habria servido de nada.
    """

    if not user.is_locked:
        raise ConflictError("La cuenta no esta bloqueada")

    user.locked_at = None
    user.failed_login_attempts = 0
    trail.audit(
        db,
        action=AuditAction.ACCOUNT_UNLOCKED,
        entity_type="users",
        entity_id=user.id,
        actor=actor,
        metadata={"username": user.username, "reason": reason},
    )
    commit(db)
    db.refresh(user)
    return user


def lock_user(db: Session, user: User, *, actor: User, reason: str | None) -> User:
    """Lock an account manually, without waiting for failed attempts."""

    if user.is_locked:
        raise ConflictError("La cuenta ya esta bloqueada")
    if user.id == actor.id:
        # Un administrador que se bloquea a si mismo necesitaria a otro
        # administrador para volver a entrar. Se evita el paso en falso.
        raise ConflictError("Un administrador no puede bloquear su propia cuenta")

    user.locked_at = datetime.now(timezone.utc)
    trail.audit(
        db,
        action=AuditAction.ACCOUNT_LOCKED,
        entity_type="users",
        entity_id=user.id,
        actor=actor,
        metadata={"username": user.username, "reason": reason, "manual": True},
    )
    commit(db)
    db.refresh(user)
    return user


def create_user(db: Session, data: AccountCreate, *, actor: User) -> User:
    """Create an account, linked to what its role requires.

    La contrasena se guarda solo como hash y no aparece en la auditoria: el
    registro dice quien creo que cuenta y con que rol, nunca con que clave.
    """

    if db.scalar(select(User.id).where(User.username == data.username)):
        raise ConflictError("Ya existe una cuenta con ese usuario")

    organization_id = None
    patient_id = None
    full_name = (data.full_name or "").strip()
    email = (data.email or "").strip() or None

    if data.role in (RoleCode.IPS_CLINICAL_OPERATOR, RoleCode.EPS_COORDINATOR):
        expected = OrganizationType.IPS if data.role == RoleCode.IPS_CLINICAL_OPERATOR else OrganizationType.EPS
        organization = db.scalar(
            select(Organization).where(
                Organization.id == data.organization_id, Organization.deleted_at.is_(None)
            )
        ) if data.organization_id else None
        if organization is None or organization.organization_type != expected:
            raise InvalidValueError(f"Este rol necesita una organizacion de tipo {expected.value}")
        organization_id = organization.id
    elif data.role == RoleCode.PATIENT:
        document = (data.patient_document or "").strip()
        patient = db.scalar(
            select(Patient).where(Patient.document_number == document, Patient.deleted_at.is_(None))
        ) if document else None
        if patient is None:
            raise InvalidValueError("No hay un paciente con ese documento")
        if db.scalar(select(User.id).where(User.patient_id == patient.id)):
            raise ConflictError("Ese paciente ya tiene una cuenta de acceso")
        patient_id = patient.id
        full_name = full_name or patient.full_name
        email = email or patient.email
    if not full_name:
        raise InvalidValueError("Escriba el nombre completo de la persona")

    role = db.scalar(select(Role).where(Role.code == data.role))
    account = User(
        username=data.username,
        full_name=full_name,
        email=email,
        password_hash=hash_password(data.password),
        role_id=role.id,
        organization_id=organization_id,
        patient_id=patient_id,
    )
    db.add(account)
    db.flush()
    trail.audit(
        db,
        action=AuditAction.CREATE,
        entity_type="users",
        entity_id=account.id,
        actor=actor,
        metadata={"username": account.username, "role": data.role.value},
    )
    commit(db)
    return get_user(db, account.id)
