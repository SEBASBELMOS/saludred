"""Encounter REST endpoints.

Institutional scope is enforced in addition to role: a clinical operator only
sees and touches encounters of its own IPS. For list endpoints the restriction
is injected into the query itself, so out-of-scope rows are not merely hidden
-- they are never fetched.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.clinical import Encounter
from app.models.enums import EncounterStatus, RoleCode
from app.schemas.audit import RecordVersionRead
from app.schemas.common import Page
from app.schemas.encounter import EncounterCreate, EncounterRead, EncounterUpdate
from app.services import encounters as encounters_service
from app.services import soft_ops

router = APIRouter(prefix="/api/v1", tags=["encuentros"])


@router.get(
    "/encounters", response_model=Page[EncounterRead], summary="Listar encuentros"
)
def list_encounters(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    patient_id: uuid.UUID | None = Query(default=None),
    organization_id: uuid.UUID | None = Query(default=None),
    status_filter: EncounterStatus | None = Query(default=None, alias="status"),
) -> Page[EncounterRead]:
    authz.require_staff(user)
    scope = authz.org_filter_for(user)
    if scope is not None:
        # The scope wins over whatever the client asked for.
        organization_id = scope
    items, total = encounters_service.list_encounters(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        organization_id=organization_id,
        status=status_filter,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/encounters",
    response_model=EncounterRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear encuentro",
)
def create_encounter(
    db: DbSession, user: CurrentUser, payload: EncounterCreate
) -> EncounterRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    authz.ensure_org_scope(user, payload.organization_id)
    return encounters_service.create_encounter(db, payload, actor=user)


@router.get(
    "/encounters/{encounter_id}",
    response_model=EncounterRead,
    summary="Consultar encuentro",
)
def get_encounter(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID
) -> EncounterRead:
    authz.require_staff(user)
    encounter = encounters_service.get_encounter(db, encounter_id)
    authz.ensure_org_scope(user, encounter.organization_id)
    return encounter


@router.put(
    "/encounters/{encounter_id}",
    response_model=EncounterRead,
    summary="Actualizar encuentro (soft edit con historial)",
)
def update_encounter(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID, payload: EncounterUpdate
) -> EncounterRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    encounter = encounters_service.get_encounter(db, encounter_id)
    authz.ensure_org_scope(user, encounter.organization_id)
    authz.ensure_owner_or_admin(user, encounter.created_by)
    return encounters_service.update_encounter(db, encounter, payload, actor=user)


@router.delete(
    "/encounters/{encounter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar encuentro (borrado logico)",
)
def delete_encounter(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID
) -> Response:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    encounter = encounters_service.get_encounter(db, encounter_id)
    authz.ensure_org_scope(user, encounter.organization_id)
    authz.ensure_owner_or_admin(user, encounter.created_by)
    encounters_service.soft_delete_encounter(db, encounter, actor=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/encounters/{encounter_id}/restore",
    response_model=EncounterRead,
    summary="Restaurar encuentro eliminado (solo Admin)",
)
def restore_encounter(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID
) -> EncounterRead:
    authz.require_admin(user)
    encounter = soft_ops.get_including_deleted(
        db, Encounter, encounter_id, label="Encuentro"
    )
    soft_ops.restore(db, encounter, actor=user)
    return encounter


@router.get(
    "/encounters/{encounter_id}/history",
    response_model=list[RecordVersionRead],
    summary="Historial de versiones del encuentro",
)
def encounter_history(
    db: DbSession, user: CurrentUser, encounter_id: uuid.UUID
) -> list[RecordVersionRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    soft_ops.get_including_deleted(db, Encounter, encounter_id, label="Encuentro")
    return soft_ops.list_history(db, Encounter, encounter_id)


@router.get(
    "/patients/{patient_id}/encounters",
    response_model=list[EncounterRead],
    summary="Encuentros de un paciente",
)
def list_patient_encounters(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> list[EncounterRead]:
    authz.require_staff(user)
    return encounters_service.list_encounters_for_patient(
        db, patient_id, organization_id=authz.org_filter_for(user)
    )
