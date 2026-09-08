"""Bed capacity and coordination endpoints.

Estos endpoints responden la pregunta que da origen al proyecto: donde hay una
cama libre, quien esta esperando, y en que orden deben atenderse.

Autorizacion, en resumen:

- Consultar capacidad y camas: cualquier rol institucional. El coordinador ve
  toda la red; el operador clinico, solo su IPS.
- Crear una solicitud: el operador clinico de la IPS que atiende al paciente.
- Asignar y liberar camas: el coordinador de EPS, que es quien tiene la vista
  completa de la red, y el administrador.
- Cambiar el estado operativo de una cama: el operador de esa IPS, porque es
  quien sabe si ya se limpio o si esta fuera de servicio.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import BedStatus, RoleCode
from app.models.organization import Location
from app.schemas.beds import (
    BedAssignRequest,
    BedAssignmentRead,
    BedCandidate,
    BedRead,
    BedRequestCreate,
    BedRequestRead,
    BedStatusChange,
    BedStatusEventRead,
    NetworkCapacity,
    OrganizationCapacity,
    QueuedBedRequest,
)
from app.services import beds as beds_service
from app.services import organizations as organizations_service
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/v1", tags=["camas"])


def _bed_or_404(db: DbSession, location_id: uuid.UUID) -> Location:
    cama = db.get(Location, location_id)
    if cama is None or cama.deleted_at is not None:
        raise NotFoundError("Cama no encontrada")
    return cama


# ---------------------------------------------------------------------------
# Capacidad
# ---------------------------------------------------------------------------


@router.get(
    "/network/capacity",
    response_model=NetworkCapacity,
    summary="Capacidad de camas de toda la red",
)
def network_capacity(db: DbSession, user: CurrentUser) -> NetworkCapacity:
    """Cuantas camas hay y cuantas estan libres, por institucion y servicio."""

    authz.require_staff(user)
    # El operador clinico ve la capacidad de su propia IPS; los roles de red,
    # la de todas.
    return beds_service.network_capacity(
        db, organization_id=authz.org_filter_for(user)
    )


@router.get(
    "/organizations/{organization_id}/capacity",
    response_model=OrganizationCapacity,
    summary="Capacidad de camas de una institucion",
)
def organization_capacity(
    db: DbSession, user: CurrentUser, organization_id: uuid.UUID
) -> OrganizationCapacity:
    authz.require_staff(user)
    authz.ensure_org_scope(user, organization_id)
    organizacion = organizations_service.get_organization(db, organization_id)
    return beds_service.organization_capacity(db, organizacion)


@router.get(
    "/organizations/{organization_id}/beds",
    response_model=list[BedRead],
    summary="Camas de una institucion",
)
def list_beds(
    db: DbSession,
    user: CurrentUser,
    organization_id: uuid.UUID,
    status: BedStatus | None = Query(default=None, description="Filtrar por estado"),
    service: str | None = Query(default=None, description="Filtrar por servicio"),
) -> list[BedRead]:
    authz.require_staff(user)
    authz.ensure_org_scope(user, organization_id)
    organizations_service.get_organization(db, organization_id)
    return beds_service.list_beds(db, organization_id, status=status, service=service)


# ---------------------------------------------------------------------------
# Solicitudes y cola
# ---------------------------------------------------------------------------


@router.post(
    "/bed-requests",
    response_model=BedRequestRead,
    status_code=201,
    summary="Solicitar una cama para un encuentro",
)
def create_bed_request(
    db: DbSession, user: CurrentUser, payload: BedRequestCreate
) -> BedRequestRead:
    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    return beds_service.create_bed_request(db, payload, actor=user)


@router.get(
    "/bed-requests/queue",
    response_model=list[QueuedBedRequest],
    summary="Cola de espera ordenada por prioridad",
)
def bed_request_queue(
    db: DbSession,
    user: CurrentUser,
    organization_id: uuid.UUID | None = Query(default=None),
    service: str | None = Query(default=None),
) -> list[QueuedBedRequest]:
    """Quien sigue. Ordenada por prioridad clinica y, a igual prioridad, por
    tiempo de espera."""

    authz.require_staff(user)
    ambito = authz.org_filter_for(user)
    return beds_service.list_queue(
        db, organization_id=ambito or organization_id, service=service
    )


@router.get(
    "/bed-requests/{request_id}",
    response_model=BedRequestRead,
    summary="Consultar una solicitud",
)
def get_bed_request(
    db: DbSession, user: CurrentUser, request_id: uuid.UUID
) -> BedRequestRead:
    authz.require_staff(user)
    return beds_service.get_bed_request(db, request_id)


@router.get(
    "/bed-requests/{request_id}/candidates",
    response_model=list[BedCandidate],
    summary="Camas que podrian servir a una solicitud",
)
def bed_candidates(
    db: DbSession, user: CurrentUser, request_id: uuid.UUID
) -> list[BedCandidate]:
    """Camas libres, ordenadas: primero las del servicio pedido."""

    authz.require_staff(user)
    solicitud = beds_service.get_bed_request(db, request_id)
    return beds_service.list_candidates(db, solicitud)


@router.post(
    "/bed-requests/{request_id}/assign",
    response_model=BedAssignmentRead,
    status_code=201,
    summary="Asignar una cama a una solicitud",
)
def assign_bed(
    db: DbSession,
    user: CurrentUser,
    request_id: uuid.UUID,
    payload: BedAssignRequest,
) -> BedAssignmentRead:
    """Asigna la cama y ocupa el recurso.

    Devuelve `409` si hay otra solicitud que deba atenderse antes. Ese rechazo
    es la regla de priorizacion actuando: se puede saltar con
    `override_priority`, pero exige una razon y queda auditado.
    """

    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    solicitud = beds_service.get_bed_request(db, request_id)
    return beds_service.assign_bed(
        db,
        solicitud,
        payload.location_id,
        actor=user,
        override_priority=payload.override_priority,
        reason=payload.reason,
    )


@router.post(
    "/bed-assignments/{assignment_id}/release",
    response_model=BedAssignmentRead,
    summary="Liberar una cama ocupada",
)
def release_bed(
    db: DbSession, user: CurrentUser, assignment_id: uuid.UUID
) -> BedAssignmentRead:
    """La cama pasa a limpieza, no directamente a disponible."""

    authz.require_role(user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)
    from app.models.beds import BedAssignment

    asignacion = db.get(BedAssignment, assignment_id)
    if asignacion is None or asignacion.deleted_at is not None:
        raise NotFoundError("Asignacion no encontrada")
    authz.ensure_org_scope(user, asignacion.location.organization_id)
    return beds_service.release_bed(db, asignacion, actor=user)


# ---------------------------------------------------------------------------
# Estado operativo de una cama
# ---------------------------------------------------------------------------


@router.post(
    "/beds/{location_id}/status",
    response_model=BedRead,
    summary="Cambiar el estado operativo de una cama",
)
def change_bed_status(
    db: DbSession,
    user: CurrentUser,
    location_id: uuid.UUID,
    payload: BedStatusChange,
) -> BedRead:
    """Limpieza terminada, cama bloqueada, fuera de servicio.

    No sirve para ocupar una cama: eso ocurre al asignar una solicitud.
    """

    authz.require_role(
        user, RoleCode.ADMIN, RoleCode.EPS_COORDINATOR, RoleCode.IPS_CLINICAL_OPERATOR
    )
    cama = _bed_or_404(db, location_id)
    authz.ensure_org_scope(user, cama.organization_id)
    return beds_service.change_bed_status(
        db, cama, payload.new_status, actor=user, reason=payload.reason
    )


@router.get(
    "/beds/{location_id}/history",
    response_model=list[BedStatusEventRead],
    summary="Historico de estados de una cama",
)
def bed_history(
    db: DbSession, user: CurrentUser, location_id: uuid.UUID
) -> list[BedStatusEventRead]:
    """Cada transicion, con quien la hizo y cuando."""

    authz.require_staff(user)
    cama = _bed_or_404(db, location_id)
    authz.ensure_org_scope(user, cama.organization_id)
    return beds_service.list_bed_history(db, location_id)
