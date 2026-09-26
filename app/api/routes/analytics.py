"""Analytics endpoints: statistics, cohorts and decision support.

Son de solo lectura y estan reservados a los roles de red: el administrador y
el coordinador de EPS. Un operador de una sola IPS no necesita --ni deberia
ver-- las cifras agregadas de instituciones que no son la suya.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import RoleCode
from app.services import analytics as analytics_service
from app.services import clustering as clustering_service

router = APIRouter(prefix="/api/v1/analytics", tags=["analitica"])

NETWORK_ROLES = (RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)


@router.get(
    "/wait-times",
    summary="Tiempos de espera por prioridad",
)
def wait_times(
    db: DbSession,
    user: CurrentUser,
    organization_id: uuid.UUID | None = Query(default=None),
) -> dict[str, Any]:
    """Cuanto espera una solicitud antes de recibir cama, segun su prioridad.

    Mide si la priorizacion funciona: si una urgencia espera lo mismo que una
    consulta de rutina, la regla no se esta aplicando.
    """

    authz.require_role(user, *NETWORK_ROLES)
    return analytics_service.wait_time_statistics(db, organization_id=organization_id)


@router.get(
    "/bed-turnaround",
    summary="Tiempo de alistamiento de camas",
)
def bed_turnaround(
    db: DbSession,
    user: CurrentUser,
    organization_id: uuid.UUID | None = Query(default=None),
) -> dict[str, Any]:
    """Cuanto tarda una cama en volver a estar disponible despues de un alta.

    Se reconstruye desde el historico de estados, emparejando cada paso a
    limpieza con el siguiente paso a disponible.
    """

    authz.require_role(user, *NETWORK_ROLES)
    return analytics_service.bed_turnaround_statistics(
        db, organization_id=organization_id
    )


@router.get(
    "/patient-cohorts",
    summary="Cohortes de pacientes y sus signos vitales",
)
def patient_cohorts(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    """Agrupa pacientes por perfil clinico y resume sus mediciones.

    Es la tabla de referencia para comprobar si un agrupamiento automatico
    descubre las mismas cohortes que la etiqueta conocida.
    """

    authz.require_role(user, *NETWORK_ROLES)
    return analytics_service.patient_cohorts(db)


@router.get(
    "/patient-clusters",
    summary="Agrupamiento automatico de pacientes por signos vitales",
)
def patient_clusters(
    db: DbSession,
    user: CurrentUser,
    k: int | None = Query(
        default=None,
        ge=2,
        le=8,
        description="Numero de grupos. Sin valor, se elige por el coeficiente de silueta.",
    ),
) -> dict[str, Any]:
    """k-means sobre signos vitales, edad e IMC, sin mirar el perfil clinico.

    El perfil solo se usa despues, para calificar el resultado con el indice de
    Rand ajustado y la pureza de cada grupo.
    """

    authz.require_role(user, *NETWORK_ROLES)
    return clustering_service.patient_clusters(db, k=k)


@router.get(
    "/occupancy-timeline",
    summary="Ingresos y altas por dia",
)
def occupancy_timeline(
    db: DbSession,
    user: CurrentUser,
    days: int = Query(default=14, ge=1, le=90),
) -> dict[str, Any]:
    """Pulso de la red: si los ingresos superan a las altas de forma sostenida,
    la saturacion no es un pico sino una tendencia."""

    authz.require_role(user, *NETWORK_ROLES)
    return analytics_service.occupancy_timeline(db, days=days)


@router.get(
    "/decision-summary",
    summary="Resumen para decidir a donde derivar",
)
def decision_summary(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    """Camas libres, cola y recomendacion por institucion, en una sola llamada.

    Con esos numeros juntos, derivar a un paciente deja de ser intuicion.
    """

    authz.require_role(user, *NETWORK_ROLES)
    return analytics_service.decision_summary(db)
