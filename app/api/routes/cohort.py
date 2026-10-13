"""Triage cohort endpoints (real data plus cluster-based synthetic rows)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.schemas.cohort import ClusterRead
from app.services import cohort as cohort_service

router = APIRouter(prefix="/api/v1/cohort", tags=["cohorte"])


@router.get(
    "/clusters",
    response_model=list[ClusterRead],
    summary="Grupos de la cohorte de triage y su lectura clinica",
)
def clusters(db: DbSession, user: CurrentUser) -> list[dict[str, object]]:
    """Cada grupo con sus filas reales y sinteticas.

    Los grupos se ordenan por tasa de ingreso: el 1 esta lejos del caso critico
    y el ultimo cerca. Si la cohorte aun no se construyo, la lista viene vacia.
    """

    authz.require_staff(user)
    return cohort_service.cluster_overview(db)
