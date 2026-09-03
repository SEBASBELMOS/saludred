"""Patient REST endpoints.

Authorization matrix applied here:

- list/read: institutional roles (patients use ``/me``)
- create: ADMIN and the clinical operator
- soft edit / soft delete: ADMIN on any record; the clinical operator only on
  records it authored
- restore: ADMIN, without exception
- history: ADMIN and the network coordinator
- ``include_deleted``: ADMIN, because seeing deleted rows is part of proving
  the delete was soft
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.enums import RoleCode
from app.models.patient import Patient
from app.schemas.audit import RecordVersionRead
from app.schemas.common import Page
from app.schemas.patient import PatientCreate, PatientRead, PatientUpdate
from app.services import patients as patients_service
from app.services import soft_ops

router = APIRouter(prefix="/api/v1/patients", tags=["pacientes"])


@router.get("", response_model=Page[PatientRead], summary="Listar pacientes")
def list_patients(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    document_number: str | None = Query(
        default=None, description="Filtro exacto por numero de documento"
    ),
    name: str | None = Query(
        default=None, description="Filtro parcial por nombre o apellido"
    ),
    include_deleted: bool = Query(
        default=False,
        description="Incluir registros eliminados logicamente (solo Admin)",
    ),
) -> Page[PatientRead]:
    authz.require_staff(user)
    if include_deleted:
        authz.require_admin(user)
    items, total = patients_service.list_patients(
        db,
        page=params.page,
        page_size=params.page_size,
        document_number=document_number,
        name=name,
        include_deleted=include_deleted,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear paciente",
)
def create_patient(
    db: DbSession, user: CurrentUser, payload: PatientCreate
) -> PatientRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    return patients_service.create_patient(db, payload, actor=user)


@router.get("/{patient_id}", response_model=PatientRead, summary="Consultar paciente")
def get_patient(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> PatientRead:
    authz.require_staff(user)
    return patients_service.get_patient(db, patient_id)


@router.put(
    "/{patient_id}",
    response_model=PatientRead,
    summary="Actualizar paciente (soft edit con historial)",
)
def update_patient(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID, payload: PatientUpdate
) -> PatientRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    patient = patients_service.get_patient(db, patient_id)
    authz.ensure_owner_or_admin(user, patient.created_by)
    return patients_service.update_patient(db, patient, payload, actor=user)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar paciente (borrado logico)",
)
def delete_patient(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> Response:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    patient = patients_service.get_patient(db, patient_id)
    authz.ensure_owner_or_admin(user, patient.created_by)
    patients_service.soft_delete_patient(db, patient, actor=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{patient_id}/restore",
    response_model=PatientRead,
    summary="Restaurar paciente eliminado (solo Admin)",
)
def restore_patient(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> PatientRead:
    authz.require_admin(user)
    patient = soft_ops.get_including_deleted(
        db, Patient, patient_id, label="Paciente"
    )
    soft_ops.restore(db, patient, actor=user)
    return patient


@router.get(
    "/{patient_id}/history",
    response_model=list[RecordVersionRead],
    summary="Historial de versiones del paciente",
)
def patient_history(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> list[RecordVersionRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    soft_ops.get_including_deleted(db, Patient, patient_id, label="Paciente")
    return soft_ops.list_history(db, Patient, patient_id)
