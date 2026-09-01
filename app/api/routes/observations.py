"""Observation REST endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, PageQuery
from app.schemas.common import Page
from app.schemas.observation import (
    ObservationCreate,
    ObservationRead,
    ObservationUpdate,
)
from app.services import encounters as encounters_service
from app.services import observations as observations_service

router = APIRouter(prefix="/api/v1", tags=["observaciones"])


@router.get(
    "/observations",
    response_model=Page[ObservationRead],
    summary="Listar observaciones",
)
def list_observations(
    db: DbSession,
    params: PageQuery,
    patient_id: uuid.UUID | None = Query(default=None),
    encounter_id: uuid.UUID | None = Query(default=None),
) -> Page[ObservationRead]:
    items, total = observations_service.list_observations(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        encounter_id=encounter_id,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/observations",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear observacion",
)
def create_observation(db: DbSession, payload: ObservationCreate) -> ObservationRead:
    return observations_service.create_observation(db, payload)


@router.get(
    "/observations/{observation_id}",
    response_model=ObservationRead,
    summary="Consultar observacion",
)
def get_observation(db: DbSession, observation_id: uuid.UUID) -> ObservationRead:
    return observations_service.get_observation(db, observation_id)


@router.put(
    "/observations/{observation_id}",
    response_model=ObservationRead,
    summary="Actualizar observacion",
)
def update_observation(
    db: DbSession, observation_id: uuid.UUID, payload: ObservationUpdate
) -> ObservationRead:
    observation = observations_service.get_observation(db, observation_id)
    return observations_service.update_observation(db, observation, payload)


@router.delete(
    "/observations/{observation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar observacion (borrado logico)",
)
def delete_observation(db: DbSession, observation_id: uuid.UUID) -> Response:
    observation = observations_service.get_observation(db, observation_id)
    observations_service.soft_delete_observation(db, observation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/encounters/{encounter_id}/observations",
    response_model=list[ObservationRead],
    summary="Observaciones de un encuentro",
)
def list_encounter_observations(
    db: DbSession, encounter_id: uuid.UUID
) -> list[ObservationRead]:
    encounters_service.get_encounter(db, encounter_id)
    return observations_service.list_observations_for_encounter(db, encounter_id)
