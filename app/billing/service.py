"""Billing rules on top of the repository.

* Scope: ``admin`` sees every invoice; ``contable`` sees those of the EPS their
  account belongs to (``users.organization_id``) and nothing else. An invoice
  of another EPS answers 404, exactly like one that does not exist.
* Automatic invoices are idempotent: the unique partial indexes turn a second
  attempt into "already billed".
* The state machine lives in ``documents.TRANSITIONS``.
* Every creation and payment-state change leaves an audit entry (R13) carrying
  ``paciente_id``, ``factura_id`` and, when there is one, ``reporte_id``.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.billing import tariffs
from app.billing.documents import (
    SYSTEM_USER,
    TRANSITIONS,
    EstadoPago,
    Origen,
    Servicio,
    new_document,
)
from app.billing.store import FacturaRepository, get_repository, parse_id
from app.core import authz
from app.events.bus import DomainEvent, publish
from app.models.ai import AiReport
from app.models.beds import BedAssignment, BedRequest
from app.models.clinical import Encounter
from app.models.enums import (
    AiReportStatus,
    AuditAction,
    BedAssignmentStatus,
    OrganizationType,
    ReferralStatus,
    RoleCode,
)
from app.models.identity import User
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.models.referral import Referral
from app.schemas.billing import FacturaCreate
from app.services import recipients, trail
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
    commit,
)

logger = logging.getLogger(__name__)

ENTITY = "facturas"
EVENT_CREATED = "factura_creada"
EVENT_RECONCILED = "facturas_conciliadas"

SERVICE_LABELS = {
    Servicio.ANALISIS_IA: "analisis de IA",
    Servicio.DIA_CAMA: "dia cama",
    Servicio.REMISION: "remision",
    Servicio.CONSULTA: "consulta",
    Servicio.MEDICAMENTO: "medicamento",
}


@dataclass(frozen=True)
class Billed:
    """Outcome of an automatic billing attempt."""

    factura: dict[str, Any]
    created: bool


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def scope_eps(user: User) -> uuid.UUID | None:
    """Gate to ``admin`` and ``contable``; return the EPS filter (None = all)."""

    authz.require_role(user, RoleCode.ADMIN, RoleCode.ACCOUNTANT)
    if user.role.code == RoleCode.ADMIN:
        return None
    if user.organization_id is None:
        raise ForbiddenError("La cuenta contable no esta asociada a una EPS")
    return user.organization_id


def _in_scope(document: dict[str, Any], eps: uuid.UUID | None) -> bool:
    return eps is None or document.get("eps_id") == str(eps)


def _document_in_scope(
    repo: FacturaRepository, user: User, factura_id: str
) -> dict[str, Any]:
    eps = scope_eps(user)
    object_id = parse_id(factura_id)
    document = repo.get(object_id) if object_id is not None else None
    if document is None or not _in_scope(document, eps):
        raise NotFoundError("Factura no encontrada")
    return document


def _actor_label(user: User | None) -> str:
    return str(user.id) if user is not None else SYSTEM_USER


# ---------------------------------------------------------------------------
# Audit and announcement
# ---------------------------------------------------------------------------


def _audit(
    db: Session,
    action: AuditAction,
    document: dict[str, Any],
    actor: User | None,
    **extra: object,
) -> None:
    trail.audit(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=None,  # the invoice id is an ObjectId, not a UUID
        actor=actor,
        metadata={
            "paciente_id": document["paciente_id"],
            "factura_id": document["id"],
            "reporte_id": document.get("reporte_id"),
            "servicio": document["servicio"],
            "valor": document["valor"],
            "estado_pago": document["estado_pago"],
            **extra,
        },
    )
    commit(db)


def announce_created(db: Session, document: dict[str, Any]) -> None:
    """Tell the accountants of the patient's EPS, and the admins (R19 flow 3).

    Not clinical, so the bus does not filter the ``contable`` role out. The
    medico, the especialista and the patient are simply never recipients.
    """

    eps = uuid.UUID(document["eps_id"]) if document.get("eps_id") else None
    servicio = Servicio(document["servicio"])
    publish(
        DomainEvent(
            tipo=EVENT_CREATED,
            mensaje=(
                f"Nueva factura de {SERVICE_LABELS[servicio]} por "
                f"${document['valor']:,} COP".replace(",", ".")
            ),
            recipients=recipients.for_billing(db, eps),
            paciente_id=uuid.UUID(document["paciente_id"]),
            clinical=False,
            datos={
                "factura_id": document["id"],
                "reporte_id": document.get("reporte_id"),
                "servicio": document["servicio"],
                "valor": document["valor"],
                "estado_pago": document["estado_pago"],
            },
        ),
        db,
    )


# ---------------------------------------------------------------------------
# Automatic invoices
# ---------------------------------------------------------------------------


def _as_utc(moment: datetime) -> datetime:
    # SQLite drops the zone; a naive value in these columns is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _latest_ips(db: Session, patient_id: uuid.UUID) -> uuid.UUID | None:
    return db.scalar(recipients.latest_encounter_organization_stmt(patient_id))


def _bill(
    db: Session,
    repo: FacturaRepository,
    *,
    patient: Patient,
    ips_id: uuid.UUID | None,
    servicio: Servicio,
    descripcion: str,
    cantidad: int,
    valor_unitario: int,
    links: dict[str, uuid.UUID | None],
    via: str,
) -> Billed:
    document = new_document(
        paciente_id=patient.id,
        eps_id=patient.eps_organization_id,
        ips_id=ips_id,
        servicio=servicio,
        descripcion=descripcion,
        cantidad=cantidad,
        valor_unitario=valor_unitario,
        origen=Origen.AUTOMATICA,
        creado_por=SYSTEM_USER,
        links=links,
    )
    stored, created = repo.insert(document)
    if created:
        _audit(db, AuditAction.INVOICE_CREATED, stored, None, origen=Origen.AUTOMATICA.value, via=via)
    return Billed(stored, created)


def bill_report(db: Session, report_id: uuid.UUID, *, via: str = "evento") -> Billed | None:
    """Invoice an approved AI report. ``None`` if it is not (or no longer) approved."""

    report = db.get(AiReport, report_id)
    if report is None or report.status != AiReportStatus.APPROVED:
        return None
    patient = db.get(Patient, report.patient_id)
    if patient is None:
        return None
    return _bill(
        db,
        get_repository(),
        patient=patient,
        ips_id=_latest_ips(db, patient.id),
        servicio=Servicio.ANALISIS_IA,
        descripcion="Analisis de IA aprobado por un clinico",
        cantidad=1,
        valor_unitario=tariffs.ANALISIS_IA,
        links={"reporte_id": report.id},
        via=via,
    )


def stay_days(assigned_at: datetime, released_at: datetime) -> int:
    """Days of a stay, rounded up, at least one."""

    seconds = (_as_utc(released_at) - _as_utc(assigned_at)).total_seconds()
    return max(1, math.ceil(seconds / 86400))


def bill_bed_stay(
    db: Session, assignment_id: uuid.UUID, *, via: str = "evento"
) -> Billed | None:
    """Invoice the bed-days of a released assignment.

    ``None`` while the assignment is still active (nothing to bill yet).
    """

    assignment = db.get(BedAssignment, assignment_id)
    if (
        assignment is None
        or assignment.status != BedAssignmentStatus.RELEASED
        or assignment.released_at is None
    ):
        return None
    request = db.get(BedRequest, assignment.bed_request_id)
    encounter = db.get(Encounter, request.encounter_id) if request else None
    patient = db.get(Patient, encounter.patient_id) if encounter else None
    bed = db.get(Location, assignment.location_id)
    if patient is None or bed is None or request is None:
        return None

    days = stay_days(assignment.assigned_at, assignment.released_at)
    category, unit = tariffs.bed_day_tariff(bed.service)
    return _bill(
        db,
        get_repository(),
        patient=patient,
        ips_id=bed.organization_id,
        servicio=Servicio.DIA_CAMA,
        descripcion=f"Dia cama, tarifa {category} ({days} dia{'s' if days != 1 else ''})",
        cantidad=days,
        valor_unitario=unit,
        links={"solicitud_cama_id": request.id, "cama_id": bed.id},
        via=via,
    )


def bill_referral(
    db: Session,
    *,
    patient_id: uuid.UUID,
    remision_id: uuid.UUID,
    ips_id: uuid.UUID | None = None,
    announce: bool = True,
) -> Billed | None:
    """Invoice an accepted referral (idempotent by ``remision_id``).

    The billing reactor calls it on ``remision_aceptada``, after the referral is
    committed; the reconciliation calls it with ``announce=False`` and sends one
    summary instead.
    """

    patient = db.get(Patient, patient_id)
    if patient is None:
        return None
    billed = _bill(
        db,
        get_repository(),
        patient=patient,
        ips_id=ips_id or _latest_ips(db, patient.id),
        servicio=Servicio.REMISION,
        descripcion="Remision a especialista",
        cantidad=1,
        valor_unitario=tariffs.REMISION,
        links={"remision_id": remision_id},
        via="evento",
    )
    if billed.created and announce:
        announce_created(db, billed.factura)
    return billed


# ---------------------------------------------------------------------------
# Manual invoices and payment states
# ---------------------------------------------------------------------------


def create_manual(db: Session, user: User, data: FacturaCreate) -> dict[str, Any]:
    eps = scope_eps(user)
    patient = db.scalar(
        select(Patient).where(Patient.id == data.paciente_id, Patient.deleted_at.is_(None))
    )
    # Out of scope looks like absent, so a contable cannot probe other EPS.
    if patient is None or (eps is not None and patient.eps_organization_id != eps):
        raise NotFoundError("Paciente no encontrado")

    servicio = Servicio(data.servicio)
    unit = data.valor_unitario
    if unit is None:
        if servicio != Servicio.CONSULTA:
            raise InvalidValueError("El valor unitario es obligatorio para un medicamento")
        unit = tariffs.CONSULTA

    if data.ips_id is not None:
        ips = db.scalar(
            select(Organization).where(
                Organization.id == data.ips_id,
                Organization.organization_type == OrganizationType.IPS,
                Organization.parent_organization_id == patient.eps_organization_id,
                Organization.deleted_at.is_(None),
            )
        )
        if ips is None:
            raise InvalidValueError("La IPS indicada no pertenece a la EPS del paciente")

    document = new_document(
        paciente_id=patient.id,
        eps_id=patient.eps_organization_id,
        ips_id=data.ips_id,
        servicio=servicio,
        descripcion=data.descripcion.strip(),
        cantidad=data.cantidad,
        valor_unitario=unit,
        origen=Origen.MANUAL,
        creado_por=_actor_label(user),
    )
    stored, _created = get_repository().insert(document)
    _audit(db, AuditAction.INVOICE_CREATED, stored, user, origen=Origen.MANUAL.value)
    return stored


def _change_state(
    db: Session,
    user: User,
    factura_id: str,
    *,
    to: EstadoPago,
    action: AuditAction,
    nota: str | None,
) -> dict[str, Any]:
    repo = get_repository()
    current = _document_in_scope(repo, user, factura_id)
    previous = EstadoPago(current["estado_pago"])
    allowed_from = tuple(s for s, targets in TRANSITIONS.items() if to in targets)
    if previous not in allowed_from:
        raise ConflictError(
            f"No se puede pasar una factura de {previous.value} a {to.value}"
        )
    updated = repo.transition(
        parse_id(factura_id),  # type: ignore[arg-type]  # validated by _document_in_scope
        to=to,
        allowed_from=allowed_from,
        usuario=_actor_label(user),
        nota=nota,
    )
    if updated is None:
        # Another request changed the state between the read and the update.
        raise ConflictError("La factura cambio de estado mientras se procesaba la solicitud")
    _audit(db, action, updated, user, desde=previous.value, nota=nota)
    return updated


def pay(db: Session, user: User, factura_id: str, *, nota: str | None) -> dict[str, Any]:
    return _change_state(
        db, user, factura_id, to=EstadoPago.PAGADA, action=AuditAction.INVOICE_PAID,
        nota=(nota or "").strip() or "Pago registrado",
    )


def void(db: Session, user: User, factura_id: str, *, motivo: str) -> dict[str, Any]:
    return _change_state(
        db, user, factura_id, to=EstadoPago.ANULADA, action=AuditAction.INVOICE_VOIDED,
        nota=motivo.strip(),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get(user: User, factura_id: str) -> dict[str, Any]:
    return _document_in_scope(get_repository(), user, factura_id)


def receipt_pdf(db: Session, user: User, factura_id: str) -> tuple[bytes, dict[str, Any]]:
    """Render the receipt. The patient's name comes from PostgreSQL, now."""

    from app.billing import receipt

    document = _document_in_scope(get_repository(), user, factura_id)
    patient = db.get(Patient, uuid.UUID(document["paciente_id"]))
    eps = db.get(Organization, uuid.UUID(document["eps_id"])) if document.get("eps_id") else None
    ips = db.get(Organization, uuid.UUID(document["ips_id"])) if document.get("ips_id") else None
    content = receipt.render_pdf(
        document,
        patient_name=f"{patient.first_name} {patient.last_name}" if patient else None,
        patient_document=(
            f"{patient.document_type.value} {patient.document_number}" if patient else None
        ),
        eps_name=eps.name if eps else None,
        ips_name=ips.name if ips else None,
    )
    return content, document


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def list_invoices(
    user: User,
    *,
    estado_pago: EstadoPago | None = None,
    servicio: Servicio | None = None,
    paciente_id: uuid.UUID | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """``desde`` and ``hasta`` are whole days, both inclusive, in UTC."""

    eps = scope_eps(user)
    return get_repository().list(
        eps_id=str(eps) if eps else None,
        estado_pago=estado_pago.value if estado_pago else None,
        servicio=servicio.value if servicio else None,
        paciente_id=str(paciente_id) if paciente_id else None,
        desde=_day_start(desde) if desde else None,
        hasta=_day_start(hasta) + timedelta(days=1) if hasta else None,
        limit=limit,
    )


def summary(user: User) -> dict[str, Any]:
    eps = scope_eps(user)
    rows = get_repository().totals(eps_id=str(eps) if eps else None)

    por_estado = {s.value: {"cantidad": 0, "valor": 0} for s in EstadoPago}
    por_servicio = {s.value: {"cantidad": 0, "valor": 0} for s in Servicio}
    total = {"cantidad": 0, "valor": 0}
    for row in rows:
        bucket = por_estado[row["estado_pago"]]
        bucket["cantidad"] += row["cantidad"]
        bucket["valor"] += row["valor"]
        if row["estado_pago"] == EstadoPago.ANULADA.value:
            continue
        for target in (por_servicio[row["servicio"]], total):
            target["cantidad"] += row["cantidad"]
            target["valor"] += row["valor"]
    return {"total_facturado": total, "por_estado": por_estado, "por_servicio": por_servicio}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def approved_reports_stmt(eps_id: uuid.UUID | None) -> Select[tuple[AiReport]]:
    stmt = (
        select(AiReport)
        .join(Patient, Patient.id == AiReport.patient_id)
        .where(AiReport.status == AiReportStatus.APPROVED)
        .order_by(AiReport.reviewed_at, AiReport.id)
    )
    if eps_id is not None:
        stmt = stmt.where(Patient.eps_organization_id == eps_id)
    return stmt


def released_assignments_stmt(eps_id: uuid.UUID | None) -> Select[tuple[BedAssignment]]:
    stmt = (
        select(BedAssignment)
        .join(BedRequest, BedRequest.id == BedAssignment.bed_request_id)
        .join(Encounter, Encounter.id == BedRequest.encounter_id)
        .join(Patient, Patient.id == Encounter.patient_id)
        .where(
            BedAssignment.status == BedAssignmentStatus.RELEASED,
            BedAssignment.released_at.is_not(None),
            BedAssignment.deleted_at.is_(None),
        )
        .order_by(BedAssignment.released_at, BedAssignment.id)
    )
    if eps_id is not None:
        stmt = stmt.where(Patient.eps_organization_id == eps_id)
    return stmt


def billable_referrals_stmt(eps_id: uuid.UUID | None) -> Select[tuple[Referral]]:
    """Referrals the specialist accepted (and the ones completed afterwards)."""

    stmt = (
        select(Referral)
        .join(Patient, Patient.id == Referral.patient_id)
        .where(Referral.status.in_((ReferralStatus.ACCEPTED, ReferralStatus.COMPLETED)))
        .order_by(Referral.accepted_at, Referral.id)
    )
    if eps_id is not None:
        stmt = stmt.where(Patient.eps_organization_id == eps_id)
    return stmt


def _summary_message(count: int) -> str:
    if count == 1:
        return "Conciliacion: se creo 1 factura"
    return f"Conciliacion: se crearon {count} facturas"


def announce_reconciled(db: Session, documents: list[dict[str, Any]]) -> None:
    """One summary notification per recipient for a whole reconciliation.

    The accountants of each EPS are told how many invoices of THEIR EPS were
    created; the admins get the grand total. Nothing is sent when nothing was
    created. The live reactor for a single approval is different on purpose:
    there, one notification per invoice is exactly right.
    """

    if not documents:
        return

    def event(count: int, to: tuple[uuid.UUID, ...], docs: list[dict[str, Any]]) -> DomainEvent:
        by_service: dict[str, int] = {}
        for document in docs:
            by_service[document["servicio"]] = by_service.get(document["servicio"], 0) + 1
        return DomainEvent(
            tipo=EVENT_RECONCILED,
            mensaje=_summary_message(count),
            recipients=to,
            clinical=False,
            datos={"creadas": count, "por_servicio": by_service},
        )

    by_eps: dict[str | None, list[dict[str, Any]]] = {}
    for document in documents:
        by_eps.setdefault(document.get("eps_id"), []).append(document)
    for eps, docs in by_eps.items():
        accountants = (
            tuple(db.scalars(recipients.accountants_of_eps_stmt(uuid.UUID(eps)))) if eps else ()
        )
        if accountants:
            publish(event(len(docs), accountants, docs), db)
    admins = tuple(db.scalars(recipients.admins_stmt()))
    if admins:
        publish(event(len(documents), admins, documents), db)


def reconcile(db: Session, user: User) -> dict[str, Any]:
    """Create the invoices that reactors failed to create.

    Safe to run any number of times: what is already billed is skipped, and the
    unique indexes would reject a duplicate anyway. Each recipient gets ONE
    summary notification ("Conciliacion: se crearon N facturas"), not one per
    invoice.
    """

    eps = scope_eps(user)
    repo = get_repository()  # raises BillingUnavailableError (503) before any work

    groups: dict[str, dict[str, int]] = {}
    created_documents: list[dict[str, Any]] = []

    def run(name: str, servicio: Servicio, field: str, rows, key, bill) -> None:  # noqa: ANN001
        billed = repo.billed_keys(servicio.value, field)
        group = {"revisados": 0, "creadas": 0, "ya_facturados": 0}
        for row in rows:
            group["revisados"] += 1
            if key(row) in billed:
                group["ya_facturados"] += 1
                continue
            outcome = bill(row)
            if outcome is None:
                continue
            if outcome.created:
                group["creadas"] += 1
                created_documents.append(outcome.factura)
            else:
                group["ya_facturados"] += 1
        groups[name] = group

    run(
        "analisis_ia", Servicio.ANALISIS_IA, "reporte_id",
        list(db.scalars(approved_reports_stmt(eps))),
        lambda r: str(r.id),
        lambda r: bill_report(db, r.id, via="conciliacion"),
    )
    assignments = list(db.scalars(released_assignments_stmt(eps)))
    run(
        "dia_cama", Servicio.DIA_CAMA, "solicitud_cama_id",
        assignments,
        lambda a: str(a.bed_request_id),
        lambda a: bill_bed_stay(db, a.id, via="conciliacion"),
    )

    referrals = list(db.scalars(billable_referrals_stmt(eps)))
    run(
        "remision", Servicio.REMISION, "remision_id",
        referrals,
        lambda r: str(r.id),
        lambda r: bill_referral(
            db,
            patient_id=r.patient_id,
            remision_id=r.id,
            ips_id=db.scalar(select(User.organization_id).where(User.id == r.specialist_user_id)),
            announce=False,
        ),
    )

    # ONE summary per recipient, never one notification per invoice: a backfill
    # of hundreds of invoices must not bury the accountant's inbox.
    try:
        announce_reconciled(db, created_documents)
    except Exception:  # noqa: BLE001 -- the invoices exist; telling about them is best effort
        logger.exception("Could not announce the reconciliation summary")
    return {**groups, "total_creadas": len(created_documents)}
