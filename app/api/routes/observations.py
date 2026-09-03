"""Observation REST endpoints.

An observation has no organization column of its own: its institutional scope
is the organization of the encounter it belongs to, and every scope check here
resolves it that way.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.clinical import Observation
from app.models.enums import RoleCode
from app.schemas.audit import RecordVersionRead
from app.schemas.common import Page
from app.schemas.observation import (
    ObservationCreate,
    ObservationRead,
    ObservationUpdate,
)
from app.services import encounters as encounters_service
from app.services import observations as observations_service
from app.services import soft_ops

router = APIRouter(prefix="/api/v1", tags=["observaciones"])


@router.get(
    "/observations",
    response_model=Page[ObservationRead],
    summary="Listar observaciones",
)
def list_observations(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    patient_id: uuid.UUID | None = Query(default=None),
    encounter_id: uuid.UUID | None = Query(default=None),
) -> Page[ObservationRead]:
    authz.require_staff(user)
    items, total = observations_service.list_observations(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        encounter_id=encounter_id,
        organization_id=authz.org_filter_for(user),
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/observations",
    response_model=ObservationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear observacion",
)
def create_observation(
    db: DbSession, user: CurrentUser, payload: ObservationCreate
) -> ObservationRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    encounter = encounters_service.get_encounter(db, payload.encounter_id)
    authz.ensure_org_scope(user, encounter.organization_id)
    return observations_service.create_observation(db, payload, actor=user)


@router.get(
    "/observations/{observation_id}",
    response_model=ObservationRead,
    summary="Consultar observacion",
)
def get_observation(
    db: DbSession, user: CurrentUser, observation_id: uuid.UUID
) -> ObservationRead:
    authz.require_staff(user)
    observation = observations_service.get_observation(db, observation_id)
    authz.ensure_org_scope(user, observation.encounter.organization_id)
    return observation


@router.put(
    "/observations/{observation_id}",
    response_model=ObservationRead,
    summary="Actualizar observacion (soft edit con historial)",
)
def update_observation(
    db: DbSession,
    user: CurrentUser,
    observation_id: uuid.UUID,
    payload: ObservationUpdate,
) -> ObservationRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    observation = observations_service.get_observation(db, observation_id)
    authz.ensure_org_scope(user, observation.encounter.organization_id)
    authz.ensure_owner_or_admin(user, observation.created_by)
    return observations_service.update_observation(db, observation, payload, actor=user)


@router.delete(
    "/observations/{observation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar observacion (borrado logico)",
)
def delete_observation(
    db: DbSession, user: CurrentUser, observation_id: uuid.UUID
) -> Response:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    observation = observations_service.get_observation(db, observation_id)
    authz.ensure_org_scope(user, observation.encounter.organization_id)
    authz.ensure_owner_or_admin(user, observation.created_by)
    observations_service.soft_delete_observation(db, observation, actor=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/observations/{observation_id}/restore",
    response_model=ObservationRead,
    summary="Restaurar observacion eliminada (solo Admin)",
)
def restore_observation(
    db: DbSession, user: CurrentUser, observation_id: uuid.UUID
) -> ObservationRead:
    authz.require_admin(user)
    observation = soft_ops.get_including_deleted(
        db, Observation, observation_id, label="Observacion"
    )
    soft_ops.restore(db, observation, actor=user)
    return observation


@router.get(
    "/observations/{observation_id}/history",
    response_model=list[RecordVersionRead],
    summary="Historial de versiones de la observacion",
)
def observation_history(
    db: DbSession, user: CurrentUser, observation_id: uuid.UUID
) -> list[RecordVersionRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    soft_ops.get_including_deleted(
        db, Observation, observation_id, label="Observacion"
    )
    return soft_ops.list_history(db, Observation, observation_id)


@router.get(
    "/encounters/{encounter_id}/observations",
    response_model=list[ObservationRead],
    summary="Observaciones de un encuentro",
)
def list_encounter_observations(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID
) -> list[ObservationRead]:
    authz.require_staff(user)
    encounter = encounters_service.get_encounter(db, encounter_id)
    authz.ensure_org_scope(user, encounter.organization_id)
    return observations_service.list_observations_for_encounter(db, encounter_id)
