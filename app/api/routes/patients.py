"""Patient REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, PageQuery
from app.schemas.common import Page
from app.schemas.patient import PatientCreate, PatientRead, PatientUpdate
from app.services import patients as patients_service

router = APIRouter(prefix="/api/v1/patients", tags=["pacientes"])


@router.get("", response_model=Page[PatientRead], summary="Listar pacientes")
def list_patients(
    db: DbSession,
    params: PageQuery,
    document_number: str | None = Query(
        default=None, description="Filtro exacto por numero de documento"
    ),
    name: str | None = Query(
        default=None, description="Filtro parcial por nombre o apellido"
    ),
) -> Page[PatientRead]:
    items, total = patients_service.list_patients(
        db,
        page=params.page,
        page_size=params.page_size,
        document_number=document_number,
        name=name,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "",
    response_model=PatientRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear paciente",
)
def create_patient(db: DbSession, payload: PatientCreate) -> PatientRead:
    return patients_service.create_patient(db, payload)


@router.get("/{patient_id}", response_model=PatientRead, summary="Consultar paciente")
def get_patient(db: DbSession, patient_id: uuid.UUID) -> PatientRead:
    return patients_service.get_patient(db, patient_id)


@router.put("/{patient_id}", response_model=PatientRead, summary="Actualizar paciente")
def update_patient(
    db: DbSession, patient_id: uuid.UUID, payload: PatientUpdate
) -> PatientRead:
    patient = patients_service.get_patient(db, patient_id)
    return patients_service.update_patient(db, patient, payload)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar paciente (borrado logico)",
)
def delete_patient(db: DbSession, patient_id: uuid.UUID) -> Response:
    patient = patients_service.get_patient(db, patient_id)
    patients_service.soft_delete_patient(db, patient)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
