"""CRUD operations for patients."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.authz import CONTRACT_ROLE_NAMES
from app.models.enums import AuditAction, DocumentType, RoleCode
from app.models.identity import User
from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientUpdate
from app.services import patient_access, recipients, soft_ops, trail
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
    commit,
)


def list_patients(
    db: Session,
    *,
    page: int,
    page_size: int,
    document_number: str | None = None,
    name: str | None = None,
    include_deleted: bool = False,
) -> tuple[list[Patient], int]:
    """Page through patients, optionally filtering by document or name.

    ``include_deleted`` is an ADMIN-only view (enforced at the route): it is how
    the soft delete is demonstrated to actually be soft -- the row is still
    there, just marked.
    """

    stmt = select(Patient)
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    if document_number:
        stmt = stmt.where(Patient.document_number == document_number)
    if name:
        pattern = f"%{name.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Patient.first_name).like(pattern),
                func.lower(Patient.last_name).like(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(Patient.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    """Fetch one live patient or raise ``NotFoundError``."""

    patient = db.scalar(
        select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise NotFoundError("Paciente no encontrado")
    return patient


def create_patient(db: Session, data: PatientCreate, *, actor: User) -> Patient:
    if _exists_with_document(db, data.document_type, data.document_number):
        raise ConflictError("Ya existe un paciente con ese tipo y numero de documento")

    # The clinician who registers the patient answers for it until someone
    # else is assigned. An admin registering does not become the clinician.
    responsible = actor.id if actor.role.code in recipients.CLINICIAN_ROLES else None
    patient = Patient(
        **data.model_dump(), created_by=actor.id, responsible_clinician_id=responsible
    )
    db.add(patient)
    db.flush()
    soft_ops.audit_create(db, patient, actor=actor)
    commit(db)
    db.refresh(patient)
    return patient


def update_patient(
    db: Session, patient: Patient, data: PatientUpdate, *, actor: User
) -> Patient:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return patient

    if "document_type" in changes or "document_number" in changes:
        new_type = changes.get("document_type", patient.document_type)
        new_number = changes.get("document_number", patient.document_number)
        if _exists_with_document(db, new_type, new_number, exclude_id=patient.id):
            raise ConflictError(
                "Ya existe un paciente con ese tipo y numero de documento"
            )

    soft_ops.snapshot_before_update(
        db, patient, actor=actor, changed_fields=sorted(changes)
    )
    for field, value in changes.items():
        setattr(patient, field, value)
    patient.updated_by = actor.id

    commit(db)
    db.refresh(patient)
    return patient


def eligible_clinicians(db: Session, patient: Patient, *, actor: User) -> list[dict[str, object]]:
    """Accounts that may be named responsible clinician of ``patient``.

    Same audience as ``set_responsible_clinician``: the admin, or a medico or
    especialista of the patient's IPS. The candidates are the active medico and
    especialista accounts of the IPS where the patient has encounters; a clinician
    caller only sees colleagues of their own IPS, because that is all they may assign.
    """

    if actor.role.code == RoleCode.ADMIN:
        pass
    elif actor.role.code in recipients.CLINICIAN_ROLES:
        patient_access.ensure_staff_patient_scope(db, actor, patient.id)
    else:
        raise ForbiddenError("Solo un medico, un especialista o el administrador puede consultarlo")

    organizations = tuple(db.scalars(recipients.patient_encounter_organizations_stmt(patient.id)))
    if actor.role.code != RoleCode.ADMIN:
        organizations = tuple(o for o in organizations if o == actor.organization_id)
    if not organizations:
        return []
    return [
        {
            "id": user.id,
            "nombre": user.full_name,
            "rol": CONTRACT_ROLE_NAMES[user.role.code],
            "organizacion": organization.name,
        }
        for user, organization in db.execute(recipients.eligible_clinicians_stmt(organizations))
    ]


def set_responsible_clinician(
    db: Session, patient: Patient, clinician_id: uuid.UUID, *, actor: User
) -> Patient:
    """Assign the responsible clinician (admin, or a clinician of the patient's IPS).

    A clinician may only hand the patient to a colleague of the same IPS; the
    admin may pick any active medico or especialista. The change keeps the
    previous version and leaves its own audit entry.
    """

    if actor.role.code == RoleCode.ADMIN:
        pass
    elif actor.role.code in recipients.CLINICIAN_ROLES:
        patient_access.ensure_staff_patient_scope(db, actor, patient.id)
    else:
        raise ForbiddenError("Solo un medico, un especialista o el administrador puede cambiarlo")

    clinician = db.scalar(select(User).where(User.id == clinician_id))
    if clinician is None:
        raise NotFoundError("Clinico no encontrado")
    if not recipients.is_usable_clinician(db, clinician.id):
        raise InvalidValueError(
            "El responsable debe ser un medico o especialista con la cuenta activa"
        )
    if actor.role.code != RoleCode.ADMIN and clinician.organization_id != actor.organization_id:
        raise ForbiddenError("Solo puede asignar a un colega de su propia IPS")

    previous = patient.responsible_clinician_id
    if previous == clinician.id:
        return patient

    trail.record_version(db, patient, actor=actor, changed_fields=["responsible_clinician_id"])
    patient.responsible_clinician_id = clinician.id
    patient.updated_by = actor.id
    trail.audit(
        db,
        action=AuditAction.RESPONSIBLE_CHANGED,
        entity_type=patient.__tablename__,
        entity_id=patient.id,
        actor=actor,
        metadata={
            "patient_id": str(patient.id),
            "from": str(previous) if previous else None,
            "to": str(clinician.id),
        },
    )
    commit(db)
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient: Patient, *, actor: User) -> None:
    """Mark the patient as deleted. The row is kept for the restore flow."""

    soft_ops.soft_delete(db, patient, actor=actor)


def _exists_with_document(
    db: Session,
    document_type: DocumentType,
    document_number: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    stmt = select(Patient.id).where(
        Patient.document_type == document_type,
        Patient.document_number == document_number,
        Patient.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Patient.id != exclude_id)
    return db.scalar(stmt.limit(1)) is not None
