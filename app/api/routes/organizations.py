"""Organization REST endpoints.

The network topology (EPS and IPS) is administrative master data: every
institutional role may read it, only ADMIN may change it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.enums import OrganizationType, RoleCode
from app.models.organization import Organization
from app.schemas.audit import RecordVersionRead
from app.schemas.common import Page
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services import organizations as organizations_service
from app.services import soft_ops

router = APIRouter(prefix="/api/v1", tags=["organizaciones"])


@router.get(
    "/organizations",
    response_model=Page[OrganizationRead],
    summary="Listar organizaciones",
)
def list_organizations(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    organization_type: OrganizationType | None = Query(default=None),
) -> Page[OrganizationRead]:
    authz.require_staff(user)
    items, total = organizations_service.list_organizations(
        db,
        page=params.page,
        page_size=params.page_size,
        organization_type=organization_type,
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/organizations",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear organizacion (solo Admin)",
)
def create_organization(
    db: DbSession, user: CurrentUser, payload: OrganizationCreate
) -> OrganizationRead:
    authz.require_admin(user)
    return organizations_service.create_organization(db, payload, actor=user)


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationRead,
    summary="Consultar organizacion",
)
def get_organization(
    db: DbSession, user: CurrentUser, organization_id: uuid.UUID
) -> OrganizationRead:
    authz.require_staff(user)
    return organizations_service.get_organization(db, organization_id)


@router.put(
    "/organizations/{organization_id}",
    response_model=OrganizationRead,
    summary="Actualizar organizacion (solo Admin, soft edit con historial)",
)
def update_organization(
    db: DbSession,
    user: CurrentUser,
    organization_id: uuid.UUID,
    payload: OrganizationUpdate,
) -> OrganizationRead:
    authz.require_admin(user)
    organization = organizations_service.get_organization(db, organization_id)
    return organizations_service.update_organization(
        db, organization, payload, actor=user
    )


@router.delete(
    "/organizations/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar organizacion (solo Admin, borrado logico)",
)
def delete_organization(
    db: DbSession, user: CurrentUser, organization_id: uuid.UUID
) -> Response:
    authz.require_admin(user)
    organization = organizations_service.get_organization(db, organization_id)
    organizations_service.soft_delete_organization(db, organization, actor=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/organizations/{organization_id}/restore",
    response_model=OrganizationRead,
    summary="Restaurar organizacion eliminada (solo Admin)",
)
def restore_organization(
    db: DbSession, user: CurrentUser, organization_id: uuid.UUID
) -> OrganizationRead:
    authz.require_admin(user)
    organization = soft_ops.get_including_deleted(
        db, Organization, organization_id, label="Organizacion"
    )
    soft_ops.restore(db, organization, actor=user)
    return organization


@router.get(
    "/organizations/{organization_id}/history",
    response_model=list[RecordVersionRead],
    summary="Historial de versiones de la organizacion",
)
def organization_history(
    db: DbSession, user: CurrentUser, organization_id: uuid.UUID
) -> list[RecordVersionRead]:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    soft_ops.get_including_deleted(
        db, Organization, organization_id, label="Organizacion"
    )
    return soft_ops.list_history(db, Organization, organization_id)


@router.get(
    "/eps/{eps_id}/ips",
    response_model=list[OrganizationRead],
    summary="IPS de una EPS",
)
def list_eps_ips(
    db: DbSession, user: CurrentUser, eps_id: uuid.UUID
) -> list[OrganizationRead]:
    authz.require_staff(user)
    return organizations_service.list_ips_for_eps(db, eps_id)
