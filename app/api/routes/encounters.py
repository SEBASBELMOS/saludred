"""Encounter REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, PageQuery
from app.models.enums import EncounterStatus
from app.schemas.common import Page
from app.schemas.encounter import EncounterCreate, EncounterRead, EncounterUpdate
from app.services import encounters as encounters_service
from app.services import patients as patients_service

router = APIRouter(prefix="/api/v1", tags=["encuentros"])


@router.get(
    "/encounters", response_model=Page[EncounterRead], summary="Listar encuentros"
)
def list_encounters(
    db: DbSession,
    params: PageQuery,
    patient_id: uuid.UUID | None = Query(default=None),
    organization_id: uuid.UUID | None = Query(default=None),
    status: EncounterStatus | None = Query(default=None),
) -> Page[EncounterRead]:
    items, total = encounters_service.list_encounters(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        organization_id=organization_id,
        status=status,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/encounters",
    response_model=EncounterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear encuentro",
)
def create_encounter(db: DbSession, payload: EncounterCreate) -> EncounterRead:
    return encounters_service.create_encounter(db, payload)


@router.get(
    "/encounters/{encounter_id}",
    response_model=EncounterRead,
    summary="Consultar encuentro",
)
def get_encounter(db: DbSession, encounter_id: uuid.UUID) -> EncounterRead:
    return encounters_service.get_encounter(db, encounter_id)


@router.put(
    "/encounters/{encounter_id}",
    response_model=EncounterRead,
    summary="Actualizar encuentro",
)
def update_encounter(
    db: DbSession, encounter_id: uuid.UUID, payload: EncounterUpdate
) -> EncounterRead:
    encounter = encounters_service.get_encounter(db, encounter_id)
    return encounters_service.update_encounter(db, encounter, payload)


@router.delete(
    "/encounters/{encounter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar encuentro (borrado logico)",
)
def delete_encounter(db: DbSession, encounter_id: uuid.UUID) -> Response:
    encounter = encounters_service.get_encounter(db, encounter_id)
    encounters_service.soft_delete_encounter(db, encounter)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/patients/{patient_id}/encounters",
    response_model=list[EncounterRead],
    summary="Encuentros de un paciente",
)
def list_patient_encounters(
    db: DbSession, patient_id: uuid.UUID
) -> list[EncounterRead]:
    patients_service.get_patient(db, patient_id)
    return encounters_service.list_encounters_for_patient(db, patient_id)
