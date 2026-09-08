"""Bed capacity and coordination.

This is the operational core of the project: knowing where there is a free bed,
who is waiting, and in what order they should be served.

The ordering rule is deliberate and lives in one place, ``QUEUE_ORDER``: first
by clinical priority, then by arrival time. A patient in a more serious
condition is attended before someone who arrived earlier but is stable; among
equals, whoever waited longest goes first. Any other criterion --who asked
louder, who is closer to the coordinator-- would be arbitrary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.models.beds import BedAssignment, BedRequest, BedStatusEvent
from app.models.clinical import Encounter
from app.models.enums import (
    AuditAction,
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    LocationType,
    OrganizationType,
    Priority,
)
from app.models.identity import User
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.schemas.beds import BedRequestCreate
from app.services import soft_ops, trail
from app.services.errors import ConflictError, NotFoundError, commit

# Rango clinico de cada prioridad. Mayor numero, se atiende antes.
PRIORITY_RANK: dict[Priority, int] = {
    Priority.EMERGENCY: 3,
    Priority.URGENT: 2,
    Priority.ROUTINE: 1,
}

# Estados en los que una solicitud sigue esperando cama.
OPEN_STATUSES = (BedRequestStatus.PENDING, BedRequestStatus.IN_REVIEW)

# Orden de la cola: prioridad clinica primero, antiguedad despues.
QUEUE_ORDER = (
    case(
        {p.value: rank for p, rank in PRIORITY_RANK.items()},
        value=BedRequest.priority,
        else_=0,
    ).desc(),
    BedRequest.requested_at.asc(),
)


# ---------------------------------------------------------------------------
# Capacidad
# ---------------------------------------------------------------------------


def _bed_query(organization_id: uuid.UUID | None = None) -> Select:
    stmt = select(Location).where(
        Location.location_type == LocationType.BED,
        Location.deleted_at.is_(None),
    )
    if organization_id is not None:
        stmt = stmt.where(Location.organization_id == organization_id)
    return stmt


def list_beds(
    db: Session,
    organization_id: uuid.UUID,
    *,
    status: BedStatus | None = None,
    service: str | None = None,
) -> list[Location]:
    """Beds of one institution, optionally filtered by state or service."""

    stmt = _bed_query(organization_id)
    if status is not None:
        stmt = stmt.where(Location.status == status)
    if service is not None:
        stmt = stmt.where(Location.service == service)
    return list(db.scalars(stmt.order_by(Location.code)))


def _count_by_status(beds: list[Location]) -> dict[str, int]:
    conteo = {estado.value: 0 for estado in BedStatus}
    for bed in beds:
        if bed.status is not None:
            conteo[bed.status.value] += 1
    return conteo


def organization_capacity(
    db: Session, organization: Organization
) -> dict[str, object]:
    """Bed counts for one institution, broken down by service."""

    beds = list_beds(db, organization.id)
    conteo = _count_by_status(beds)
    total = len(beds)

    servicios: dict[str, list[Location]] = {}
    for bed in beds:
        servicios.setdefault(bed.service or "(sin servicio)", []).append(bed)

    pendientes = db.scalar(
        select(func.count())
        .select_from(BedRequest)
        .where(
            BedRequest.target_organization_id == organization.id,
            BedRequest.status.in_(OPEN_STATUSES),
            BedRequest.deleted_at.is_(None),
        )
    ) or 0

    return {
        "organization_id": organization.id,
        "organization_code": organization.code,
        "organization_name": organization.name,
        "total": total,
        "available": conteo[BedStatus.AVAILABLE.value],
        "occupied": conteo[BedStatus.OCCUPIED.value],
        "reserved": conteo[BedStatus.RESERVED.value],
        "cleaning": conteo[BedStatus.CLEANING.value],
        "blocked": conteo[BedStatus.BLOCKED.value],
        "maintenance": conteo[BedStatus.MAINTENANCE.value],
        "occupancy_rate": round(conteo[BedStatus.OCCUPIED.value] / total, 3) if total else 0.0,
        "pending_requests": pendientes,
        "services": [
            {
                "service": nombre,
                "total": len(camas),
                **{
                    clave: _count_by_status(camas)[estado.value]
                    for clave, estado in (
                        ("available", BedStatus.AVAILABLE),
                        ("occupied", BedStatus.OCCUPIED),
                        ("reserved", BedStatus.RESERVED),
                        ("cleaning", BedStatus.CLEANING),
                        ("blocked", BedStatus.BLOCKED),
                        ("maintenance", BedStatus.MAINTENANCE),
                    )
                },
            }
            for nombre, camas in sorted(servicios.items())
        ],
    }


def network_capacity(
    db: Session, *, organization_id: uuid.UUID | None = None
) -> dict[str, object]:
    """Capacity across the network, or restricted to one institution."""

    stmt = select(Organization).where(
        Organization.organization_type == OrganizationType.IPS,
        Organization.deleted_at.is_(None),
    )
    if organization_id is not None:
        stmt = stmt.where(Organization.id == organization_id)
    instituciones = list(db.scalars(stmt.order_by(Organization.name)))

    detalle = [organization_capacity(db, ips) for ips in instituciones]
    total = sum(d["total"] for d in detalle)
    ocupadas = sum(d["occupied"] for d in detalle)

    return {
        "total": total,
        "available": sum(d["available"] for d in detalle),
        "occupied": ocupadas,
        "occupancy_rate": round(ocupadas / total, 3) if total else 0.0,
        "pending_requests": sum(d["pending_requests"] for d in detalle),
        "organizations": detalle,
    }


# ---------------------------------------------------------------------------
# Solicitudes de cama
# ---------------------------------------------------------------------------


def get_bed_request(db: Session, request_id: uuid.UUID) -> BedRequest:
    solicitud = db.scalar(
        select(BedRequest).where(
            BedRequest.id == request_id, BedRequest.deleted_at.is_(None)
        )
    )
    if solicitud is None:
        raise NotFoundError("Solicitud de cama no encontrada")
    return solicitud


def create_bed_request(
    db: Session, data: BedRequestCreate, *, actor: User
) -> BedRequest:
    """Open a bed request for an ongoing encounter.

    The requesting institution is taken from the encounter, not from the
    payload: quien pide una cama es la institucion que esta atendiendo al
    paciente, y eso ya esta registrado. Aceptarlo del cliente permitiria pedir
    en nombre de otra IPS.
    """

    encuentro = db.scalar(
        select(Encounter).where(
            Encounter.id == data.encounter_id, Encounter.deleted_at.is_(None)
        )
    )
    if encuentro is None:
        raise NotFoundError("Encuentro referenciado no encontrado")

    abierta = db.scalar(
        select(BedRequest.id).where(
            BedRequest.encounter_id == data.encounter_id,
            BedRequest.status.in_(OPEN_STATUSES),
            BedRequest.deleted_at.is_(None),
        )
    )
    if abierta is not None:
        raise ConflictError(
            "El encuentro ya tiene una solicitud de cama sin resolver"
        )

    if data.target_organization_id is not None:
        destino = db.scalar(
            select(Organization).where(
                Organization.id == data.target_organization_id,
                Organization.deleted_at.is_(None),
            )
        )
        if destino is None:
            raise NotFoundError("Institucion destino no encontrada")
        if destino.organization_type != OrganizationType.IPS:
            raise ConflictError("El destino de una solicitud debe ser una IPS")

    solicitud = BedRequest(
        encounter_id=data.encounter_id,
        requesting_organization_id=encuentro.organization_id,
        target_organization_id=data.target_organization_id,
        required_service=data.required_service,
        priority=data.priority,
        status=BedRequestStatus.PENDING,
        notes=data.notes,
        created_by=actor.id,
    )
    db.add(solicitud)
    db.flush()
    soft_ops.audit_create(db, solicitud, actor=actor)
    commit(db)
    db.refresh(solicitud)
    return solicitud


def list_queue(
    db: Session,
    *,
    organization_id: uuid.UUID | None = None,
    service: str | None = None,
) -> list[dict[str, object]]:
    """The waiting queue, ordered by clinical priority and then by wait time.

    Returns the position of each request so the answer to "who goes next" is
    explicit and not something the caller has to infer.
    """

    stmt = (
        select(BedRequest, Patient, Encounter)
        .join(Encounter, BedRequest.encounter_id == Encounter.id)
        .join(Patient, Encounter.patient_id == Patient.id)
        .where(
            BedRequest.status.in_(OPEN_STATUSES),
            BedRequest.deleted_at.is_(None),
        )
    )
    if organization_id is not None:
        stmt = stmt.where(
            (BedRequest.target_organization_id == organization_id)
            | (BedRequest.requesting_organization_id == organization_id)
        )
    if service is not None:
        stmt = stmt.where(BedRequest.required_service == service)

    ahora = datetime.now(timezone.utc)
    filas = db.execute(stmt.order_by(*QUEUE_ORDER)).all()

    cola: list[dict[str, object]] = []
    for posicion, (solicitud, paciente, _encuentro) in enumerate(filas, start=1):
        espera = ahora - solicitud.requested_at
        cola.append(
            {
                "id": solicitud.id,
                "encounter_id": solicitud.encounter_id,
                "requesting_organization_id": solicitud.requesting_organization_id,
                "target_organization_id": solicitud.target_organization_id,
                "required_service": solicitud.required_service,
                "priority": solicitud.priority,
                "status": solicitud.status,
                "notes": solicitud.notes,
                "requested_at": solicitud.requested_at,
                "resolved_at": solicitud.resolved_at,
                "queue_position": posicion,
                "patient_name": paciente.full_name,
                "patient_document": paciente.business_identifier,
                "waiting_minutes": int(espera.total_seconds() // 60),
            }
        )
    return cola


def list_candidates(db: Session, solicitud: BedRequest) -> list[dict[str, object]]:
    """Beds that could serve a request, best first.

    Ranking: primero las del servicio pedido, despues las de la institucion
    solicitante. Una cama de otro servicio sirve en una urgencia, pero no es
    la primera opcion.
    """

    stmt = _bed_query().where(Location.status == BedStatus.AVAILABLE)
    if solicitud.target_organization_id is not None:
        stmt = stmt.where(Location.organization_id == solicitud.target_organization_id)

    candidatas = list(db.scalars(stmt))
    orgs = {
        o.id: o
        for o in db.scalars(
            select(Organization).where(
                Organization.id.in_({c.organization_id for c in candidatas})
            )
        )
    } if candidatas else {}

    resultado = [
        {
            "location_id": cama.id,
            "code": cama.code,
            "name": cama.name,
            "service": cama.service,
            "organization_id": cama.organization_id,
            "organization_name": orgs[cama.organization_id].name,
            "same_service": cama.service == solicitud.required_service,
            "same_organization": cama.organization_id
            == solicitud.requesting_organization_id,
        }
        for cama in candidatas
    ]
    resultado.sort(
        key=lambda c: (not c["same_service"], not c["same_organization"], c["code"])
    )
    return resultado


# ---------------------------------------------------------------------------
# Asignacion
# ---------------------------------------------------------------------------


def _more_urgent_waiting(
    db: Session, solicitud: BedRequest
) -> BedRequest | None:
    """A request that should be served before this one, if any.

    Compara solo contra solicitudes que compiten por la misma cama: mismo
    servicio y misma institucion destino. Una urgencia de otra IPS no bloquea
    una asignacion aqui.
    """

    rango = PRIORITY_RANK[solicitud.priority]
    stmt = select(BedRequest).where(
        BedRequest.id != solicitud.id,
        BedRequest.status.in_(OPEN_STATUSES),
        BedRequest.deleted_at.is_(None),
        BedRequest.required_service == solicitud.required_service,
    )
    if solicitud.target_organization_id is not None:
        stmt = stmt.where(
            BedRequest.target_organization_id == solicitud.target_organization_id
        )

    for otra in db.scalars(stmt.order_by(*QUEUE_ORDER)):
        otro_rango = PRIORITY_RANK[otra.priority]
        if otro_rango > rango:
            return otra
        if otro_rango == rango and otra.requested_at < solicitud.requested_at:
            return otra
    return None


def assign_bed(
    db: Session,
    solicitud: BedRequest,
    location_id: uuid.UUID,
    *,
    actor: User,
    override_priority: bool = False,
    reason: str | None = None,
) -> BedAssignment:
    """Assign a bed to a request.

    Validaciones, en orden: la solicitud sigue abierta, la cama existe y esta
    libre, la cama pertenece a la institucion destino, y no hay otra solicitud
    que deba atenderse antes.

    El orden importa: se comprueba primero lo barato y lo que da mejor mensaje
    de error.
    """

    if solicitud.status not in OPEN_STATUSES:
        raise ConflictError(
            f"La solicitud ya esta en estado {solicitud.status.value}"
        )

    cama = db.scalar(
        select(Location).where(
            Location.id == location_id, Location.deleted_at.is_(None)
        )
    )
    if cama is None:
        raise NotFoundError("Cama no encontrada")
    if cama.location_type != LocationType.BED:
        raise ConflictError("La ubicacion indicada no es una cama")
    if cama.status != BedStatus.AVAILABLE:
        raise ConflictError(
            f"La cama {cama.code} no esta disponible (estado {cama.status.value})"
        )
    if (
        solicitud.target_organization_id is not None
        and cama.organization_id != solicitud.target_organization_id
    ):
        raise ConflictError(
            "La cama no pertenece a la institucion destino de la solicitud"
        )

    prioritaria = _more_urgent_waiting(db, solicitud)
    if prioritaria is not None and not override_priority:
        raise ConflictError(
            "Hay una solicitud que debe atenderse primero: "
            f"{prioritaria.id} (prioridad {prioritaria.priority.value}, "
            f"en espera desde {prioritaria.requested_at.isoformat()}). "
            "Para asignar igualmente, enviar override_priority=true con una razon."
        )
    if prioritaria is not None and not reason:
        raise ConflictError(
            "Saltar el orden de prioridad exige indicar una razon"
        )

    asignacion = BedAssignment(
        bed_request_id=solicitud.id,
        location_id=cama.id,
        status=BedAssignmentStatus.ACTIVE,
        created_by=actor.id,
    )
    db.add(asignacion)

    anterior = cama.status
    cama.status = BedStatus.OCCUPIED
    cama.updated_by = actor.id

    solicitud.status = BedRequestStatus.ASSIGNED
    solicitud.target_organization_id = cama.organization_id
    solicitud.resolved_at = datetime.now(timezone.utc)
    solicitud.updated_by = actor.id

    # La cama ocupada se refleja en el encuentro: es lo que despues viaja a
    # FHIR como Encounter.location.
    encuentro = db.get(Encounter, solicitud.encounter_id)
    if encuentro is not None:
        encuentro.location_id = cama.id

    db.add(
        BedStatusEvent(
            location_id=cama.id,
            encounter_id=solicitud.encounter_id,
            previous_status=anterior,
            new_status=BedStatus.OCCUPIED,
            reason=reason or "Asignacion de cama a solicitud",
            changed_by=actor.id,
        )
    )
    db.flush()
    trail.audit(
        db,
        action=AuditAction.CREATE,
        entity_type="bed_assignments",
        entity_id=asignacion.id,
        actor=actor,
        metadata={
            "bed_request_id": str(solicitud.id),
            "bed_code": cama.code,
            "priority": solicitud.priority.value,
            "override_priority": override_priority,
            "reason": reason,
        },
    )
    commit(db)
    db.refresh(asignacion)
    return asignacion


def release_bed(
    db: Session, asignacion: BedAssignment, *, actor: User, reason: str | None = None
) -> BedAssignment:
    """Free a bed. It goes to cleaning, never straight back to available.

    Entre que un paciente se va y la cama puede recibir a otro hay un paso
    real: el alistamiento. Saltarselo seria modelar un hospital que no existe.
    """

    if asignacion.status != BedAssignmentStatus.ACTIVE:
        raise ConflictError("La asignacion ya no esta activa")

    cama = db.get(Location, asignacion.location_id)
    if cama is None:
        raise NotFoundError("Cama no encontrada")

    asignacion.status = BedAssignmentStatus.RELEASED
    asignacion.released_at = datetime.now(timezone.utc)
    asignacion.updated_by = actor.id

    anterior = cama.status
    cama.status = BedStatus.CLEANING
    cama.updated_by = actor.id

    db.add(
        BedStatusEvent(
            location_id=cama.id,
            encounter_id=asignacion.bed_request.encounter_id,
            previous_status=anterior,
            new_status=BedStatus.CLEANING,
            reason=reason or "Cama liberada, pendiente de alistamiento",
            changed_by=actor.id,
        )
    )
    commit(db)
    db.refresh(asignacion)
    return asignacion


def change_bed_status(
    db: Session,
    cama: Location,
    new_status: BedStatus,
    *,
    actor: User,
    reason: str | None = None,
) -> Location:
    """Record a bed state transition.

    Toda transicion queda en bed_status_events: es lo que permite reconstruir
    cuanto tardo una cama en pasar de alta a disponible, que es la metrica que
    este proyecto existe para poder medir.
    """

    if cama.location_type != LocationType.BED:
        raise ConflictError("Solo las camas tienen estado operativo")
    if cama.status == new_status:
        raise ConflictError(f"La cama ya esta en estado {new_status.value}")
    if new_status == BedStatus.OCCUPIED:
        raise ConflictError(
            "Una cama se ocupa asignando una solicitud, no cambiando su estado"
        )

    anterior = cama.status
    cama.status = new_status
    cama.updated_by = actor.id
    db.add(
        BedStatusEvent(
            location_id=cama.id,
            previous_status=anterior,
            new_status=new_status,
            reason=reason,
            changed_by=actor.id,
        )
    )
    commit(db)
    db.refresh(cama)
    return cama


def list_bed_history(db: Session, location_id: uuid.UUID) -> list[BedStatusEvent]:
    """Every state transition of one bed, newest first."""

    return list(
        db.scalars(
            select(BedStatusEvent)
            .where(BedStatusEvent.location_id == location_id)
            .order_by(BedStatusEvent.event_at.desc())
        )
    )
