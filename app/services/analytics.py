"""Statistics and cohort analysis over the operational data.

Todo el calculo se hace en SQL, agregando en la base. La alternativa --traer
las filas y promediarlas en Python-- funciona con datos de demostracion y deja
de funcionar cuando la tabla crece: mover miles de filas por la red para
devolver un numero es trabajo desperdiciado.

Estas consultas responden las preguntas que el proyecto existe para contestar:
cuanto se espera una cama, cuanto tarda una cama en volver a estar disponible
tras un alta, y en que se diferencian unos grupos de pacientes de otros.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, Numeric, cast, func, select
from sqlalchemy.orm import Session, aliased

from app.models.beds import BedAssignment, BedRequest, BedStatusEvent
from app.models.clinical import Observation
from app.models.enums import (
    BedRequestStatus,
    BedStatus,
    OrganizationType,
    Priority,
)
from app.models.organization import Location, Organization
from app.models.patient import Patient

# Minutos transcurridos entre dos columnas de fecha, calculados por PostgreSQL.
def _minutes_between(desde, hasta):
    return cast(func.extract("epoch", hasta - desde) / 60.0, Float)


def _round1(expresion):
    """Round to one decimal in PostgreSQL.

    ``round(x, n)`` only exists for ``numeric``: con ``double precision``, que
    es lo que devuelven ``extract(epoch ...)`` y ``percentile_cont``, Postgres
    responde "function round(double precision, integer) does not exist".
    SQLite no lo detecta, por eso las pruebas en memoria no lo veian.
    """

    return func.round(cast(expresion, Numeric), 1)


def _percentile(columna, fraccion: float):
    """Percentil continuo. La mediana resiste los valores extremos mucho mejor
    que el promedio, y en tiempos de espera los extremos son la norma."""

    return func.percentile_cont(fraccion).within_group(columna)


# ---------------------------------------------------------------------------
# Tiempos de espera
# ---------------------------------------------------------------------------


def wait_time_statistics(
    db: Session, *, organization_id: uuid.UUID | None = None
) -> dict[str, object]:
    """How long a bed request waits before it is assigned, by priority.

    Es la metrica que mide si la priorizacion sirve de algo: si una urgencia
    espera lo mismo que una consulta de rutina, la regla no se esta aplicando.
    """

    espera = _minutes_between(BedRequest.requested_at, BedAssignment.assigned_at)

    stmt = (
        select(
            BedRequest.priority.label("priority"),
            func.count().label("assignments"),
            _round1(func.avg(espera)).label("avg_minutes"),
            _round1(_percentile(espera, 0.5)).label("median_minutes"),
            _round1(func.min(espera)).label("min_minutes"),
            _round1(func.max(espera)).label("max_minutes"),
            _round1(_percentile(espera, 0.9)).label("p90_minutes"),
        )
        .select_from(BedRequest)
        .join(BedAssignment, BedAssignment.bed_request_id == BedRequest.id)
        .where(BedRequest.deleted_at.is_(None))
        .group_by(BedRequest.priority)
    )
    if organization_id is not None:
        stmt = stmt.where(BedRequest.target_organization_id == organization_id)

    # Orden clinico, no alfabetico: primero lo mas urgente.
    orden = {Priority.EMERGENCY: 0, Priority.URGENT: 1, Priority.ROUTINE: 2}
    filas = sorted(db.execute(stmt).all(), key=lambda f: orden.get(f.priority, 9))

    return {
        "by_priority": [
            {
                "priority": f.priority.value,
                "assignments": f.assignments,
                "avg_minutes": float(f.avg_minutes or 0),
                "median_minutes": float(f.median_minutes or 0),
                "p90_minutes": float(f.p90_minutes or 0),
                "min_minutes": float(f.min_minutes or 0),
                "max_minutes": float(f.max_minutes or 0),
            }
            for f in filas
        ]
    }


# ---------------------------------------------------------------------------
# Rotacion de camas
# ---------------------------------------------------------------------------


def bed_turnaround_statistics(
    db: Session, *, organization_id: uuid.UUID | None = None
) -> dict[str, object]:
    """Time a bed spends in cleaning before it is available again.

    Se reconstruye desde ``bed_status_events``: se empareja cada transicion a
    CLEANING con la siguiente transicion a AVAILABLE de la misma cama. Por eso
    esa tabla es append-only y no se edita: es la unica fuente que permite
    medir este intervalo hacia atras en el tiempo.
    """

    limpieza = aliased(BedStatusEvent)
    disponible = aliased(BedStatusEvent)

    siguiente_disponible = (
        select(func.min(disponible.event_at))
        .where(
            disponible.location_id == limpieza.location_id,
            disponible.new_status == BedStatus.AVAILABLE,
            disponible.event_at > limpieza.event_at,
        )
        .scalar_subquery()
    )

    minutos = cast(
        func.extract("epoch", siguiente_disponible - limpieza.event_at) / 60.0, Float
    )

    stmt = (
        select(
            Organization.id.label("organization_id"),
            Organization.name.label("organization_name"),
            func.count().label("cycles"),
            _round1(func.avg(minutos)).label("avg_minutes"),
            _round1(_percentile(minutos, 0.5)).label("median_minutes"),
            _round1(func.max(minutos)).label("max_minutes"),
        )
        .select_from(limpieza)
        .join(Location, Location.id == limpieza.location_id)
        .join(Organization, Organization.id == Location.organization_id)
        .where(limpieza.new_status == BedStatus.CLEANING, siguiente_disponible.is_not(None))
        .group_by(Organization.id, Organization.name)
        .order_by(Organization.name)
    )
    if organization_id is not None:
        stmt = stmt.where(Organization.id == organization_id)

    filas = db.execute(stmt).all()
    return {
        "by_organization": [
            {
                "organization_id": str(f.organization_id),
                "organization_name": f.organization_name,
                "cycles": f.cycles,
                "avg_minutes": float(f.avg_minutes or 0),
                "median_minutes": float(f.median_minutes or 0),
                "max_minutes": float(f.max_minutes or 0),
            }
            for f in filas
        ]
    }


# ---------------------------------------------------------------------------
# Cohortes de pacientes
# ---------------------------------------------------------------------------

# Signos vitales que describen una cohorte. Se eligieron porque separan
# perfiles clinicos distintos: la presion distingue al hipertenso, la glucosa
# al diabetico, la saturacion al respiratorio.
COHORT_VITALS = {
    "8480-6": "sistolica",
    "8462-4": "diastolica",
    "2339-0": "glucosa",
    "59408-5": "saturacion",
    "8867-4": "frecuencia_cardiaca",
}


def patient_cohorts(db: Session) -> dict[str, object]:
    """Group patients by clinical profile and summarise their vitals.

    Devuelve, por cohorte, cuantos pacientes hay, su edad media y el promedio
    de cada signo vital. Es la tabla que permite responder si los grupos son
    de verdad distintos entre si, y la referencia contra la cual comparar un
    agrupamiento automatico.
    """

    edad = cast(
        func.extract("year", func.age(Patient.birth_date)), Float
    )

    base = (
        select(
            Patient.clinical_profile.label("profile"),
            func.count(func.distinct(Patient.id)).label("patients"),
            _round1(func.avg(edad)).label("avg_age"),
        )
        .where(Patient.deleted_at.is_(None))
        .group_by(Patient.clinical_profile)
    )
    resumen = {f.profile: f for f in db.execute(base).all()}

    # Promedio de cada signo vital por cohorte, en una sola pasada.
    vitales = (
        select(
            Patient.clinical_profile.label("profile"),
            Observation.code.label("code"),
            _round1(func.avg(Observation.value_numeric)).label("avg"),
            _round1(_percentile(Observation.value_numeric, 0.5)).label("median"),
            func.count().label("samples"),
        )
        .select_from(Observation)
        .join(Patient, Patient.id == Observation.patient_id)
        .where(
            Observation.deleted_at.is_(None),
            Observation.value_numeric.is_not(None),
            Observation.code.in_(COHORT_VITALS),
        )
        .group_by(Patient.clinical_profile, Observation.code)
    )

    por_cohorte: dict[object, dict[str, object]] = {}
    for f in db.execute(vitales).all():
        por_cohorte.setdefault(f.profile, {})[COHORT_VITALS[f.code]] = {
            "avg": float(f.avg or 0),
            "median": float(f.median or 0),
            "samples": f.samples,
        }

    cohortes = []
    for profile, fila in resumen.items():
        cohortes.append(
            {
                "profile": profile.value if profile is not None else "SIN_PERFIL",
                "patients": fila.patients,
                "avg_age": float(fila.avg_age or 0),
                "vitals": por_cohorte.get(profile, {}),
            }
        )
    cohortes.sort(key=lambda c: -c["patients"])
    return {"cohorts": cohortes}


# ---------------------------------------------------------------------------
# Ocupacion en el tiempo
# ---------------------------------------------------------------------------


def occupancy_timeline(db: Session, *, days: int = 14) -> dict[str, object]:
    """Daily count of admissions and discharges over the last N days.

    Sirve para ver el pulso de la red: si los ingresos superan sostenidamente
    a las altas, la saturacion no es un pico, es una tendencia.
    """

    desde = datetime.now(timezone.utc) - timedelta(days=days)

    ingresos = (
        select(
            func.date_trunc("day", BedAssignment.assigned_at).label("dia"),
            func.count().label("n"),
        )
        .where(BedAssignment.assigned_at >= desde, BedAssignment.deleted_at.is_(None))
        .group_by("dia")
    )
    altas = (
        select(
            func.date_trunc("day", BedAssignment.released_at).label("dia"),
            func.count().label("n"),
        )
        .where(
            BedAssignment.released_at.is_not(None),
            BedAssignment.released_at >= desde,
            BedAssignment.deleted_at.is_(None),
        )
        .group_by("dia")
    )

    por_dia: dict[str, dict[str, int]] = {}
    for f in db.execute(ingresos).all():
        por_dia.setdefault(f.dia.date().isoformat(), {"admissions": 0, "discharges": 0})[
            "admissions"
        ] = f.n
    for f in db.execute(altas).all():
        por_dia.setdefault(f.dia.date().isoformat(), {"admissions": 0, "discharges": 0})[
            "discharges"
        ] = f.n

    serie = [{"date": d, **v} for d, v in sorted(por_dia.items())]
    return {"days": days, "timeline": serie}


# ---------------------------------------------------------------------------
# Resumen para decision
# ---------------------------------------------------------------------------


def decision_summary(db: Session) -> dict[str, object]:
    """One call with what a coordinator needs to decide where to send a patient.

    Por institucion: camas libres, cuantas solicitudes esperan, y cuanto se
    espera alli en promedio. Con esos tres numeros juntos, la decision deja de
    ser intuicion.
    """

    libres = (
        select(
            Location.organization_id.label("org"),
            func.count().label("disponibles"),
        )
        .where(
            Location.status == BedStatus.AVAILABLE,
            Location.deleted_at.is_(None),
        )
        .group_by(Location.organization_id)
        .subquery()
    )
    # La cola se agrupa por el destino si ya lo tiene, y por la institucion
    # que la origino si todavia no. Agrupar solo por el destino contaba cero
    # siempre: una solicitud nace sin destino y solo recibe uno cuando el
    # coordinador la ubica, de modo que las que estan esperando -- las unicas
    # que importan aqui -- son precisamente las que aun no lo tienen.
    org_en_cola = func.coalesce(
        BedRequest.target_organization_id, BedRequest.requesting_organization_id
    )
    esperando = (
        select(
            org_en_cola.label("org"),
            func.count().label("en_espera"),
        )
        .where(
            BedRequest.status.in_(
                (BedRequestStatus.PENDING, BedRequestStatus.IN_REVIEW)
            ),
            BedRequest.deleted_at.is_(None),
        )
        .group_by(org_en_cola)
        .subquery()
    )

    stmt = (
        select(
            Organization.id,
            Organization.name,
            func.coalesce(libres.c.disponibles, 0).label("disponibles"),
            func.coalesce(esperando.c.en_espera, 0).label("en_espera"),
        )
        .select_from(Organization)
        .outerjoin(libres, libres.c.org == Organization.id)
        .outerjoin(esperando, esperando.c.org == Organization.id)
        .where(
            Organization.organization_type == OrganizationType.IPS,
            Organization.deleted_at.is_(None),
        )
        .order_by(Organization.name)
    )

    filas = db.execute(stmt).all()
    resultado = []
    for f in filas:
        # Una institucion con camas libres y sin cola es la mejor candidata.
        # Con cola y sin camas, la peor. El indice ordena esa lectura.
        indice = f.disponibles - f.en_espera
        resultado.append(
            {
                "organization_id": str(f.id),
                "organization_name": f.name,
                "available_beds": f.disponibles,
                "waiting_requests": f.en_espera,
                "capacity_index": indice,
                "recommendation": (
                    "recibir" if indice > 0 else "saturada" if indice < 0 else "al limite"
                ),
            }
        )
    resultado.sort(key=lambda r: -r["capacity_index"])
    return {"organizations": resultado}
