"""Organization REST endpoints (EPS and IPS)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, PageQuery
from app.models.enums import OrganizationType
from app.schemas.common import Page
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.services import organizations as organizations_service

router = APIRouter(prefix="/api/v1", tags=["organizaciones"])


@router.get(
    "/organizations",
    response_model=Page[OrganizationRead],
    summary="Listar organizaciones",
)
def list_organizations(
    db: DbSession,
    params: PageQuery,
    organization_type: OrganizationType | None = Query(default=None),
) -> Page[OrganizationRead]:
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
    summary="Crear organizacion",
)
def create_organization(db: DbSession, payload: OrganizationCreate) -> OrganizationRead:
    return organizations_service.create_organization(db, payload)


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationRead,
    summary="Consultar organizacion",
)
def get_organization(db: DbSession, organization_id: uuid.UUID) -> OrganizationRead:
    return organizations_service.get_organization(db, organization_id)


@router.put(
    "/organizations/{organization_id}",
    response_model=OrganizationRead,
    summary="Actualizar organizacion",
)
def update_organization(
    db: DbSession, organization_id: uuid.UUID, payload: OrganizationUpdate
) -> OrganizationRead:
    organization = organizations_service.get_organization(db, organization_id)
    return organizations_service.update_organization(db, organization, payload)


@router.delete(
    "/organizations/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar organizacion (borrado logico)",
)
def delete_organization(db: DbSession, organization_id: uuid.UUID) -> Response:
    organization = organizations_service.get_organization(db, organization_id)
    organizations_service.soft_delete_organization(db, organization)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/eps/{eps_id}/ips",
    response_model=list[OrganizationRead],
    summary="IPS afiliadas a una EPS",
)
def list_eps_ips(db: DbSession, eps_id: uuid.UUID) -> list[OrganizationRead]:
    return organizations_service.list_ips_for_eps(db, eps_id)
