"""Critical alerts that stay on screen until somebody deals with them (R20).

Two kinds share the ``alerts`` table (see ``app.models.alert``):

``paciente_critico``
    Raised by the ``alert_reactor`` when an AI analysis CREATES a report the
    model classifies as critical (``is_critical``): not when it is approved, so
    the clinician sees it right after "ejecuta el analisis". Addressed to the
    responsible clinician, or to the fallback recipients of ``recipients.py``.
    **One ACTIVE alert per patient:** if one is already active, a new critical
    report is attached to it (``ai_report_id`` and message move to the latest
    report) and nobody is notified twice.

``cama_sin_limpiar``
    A bed is free but its cleaning has no approved evidence ("esta libre, pero
    no ha sido limpiada: esta cama esta perdiendo tiempo"). Raised by the
    monitor (``alert_monitor``), one per bed while active, addressed to the
    network coordinator and the medicos of the bed's IPS. It is acknowledged by
    the coordinator, an admin or a medico of that IPS, or resolves itself when
    the cleaning is approved (or the bed leaves CLEANING).

Who sees what (``visibility_clause``): the admin sees everything; the
coordinator sees bed alerts; a medico or especialista sees the critical alerts
addressed to them or about patients in their scope (their IPS, or a referral for
a specialist), and the medico also the bed alerts of their IPS. ``paciente`` and
``contable`` neither see nor acknowledge.

Acknowledging a CLINICAL alert is for ``medico`` and ``especialista`` only. The
admin can read every alert but never signs a clinical one, exactly like AI
reports (the person who answers for the patient is the one who acknowledges).

Escalation (R20.4): an alert still ``activa`` after ``ALERT_ESCALATION_MINUTES``
is escalated ONCE to the admins and to the other clinicians of its IPS.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core import authz
from app.events.bus import DomainEvent, publish
from app.models.ai import AiReport
from app.models.alert import Alert
from app.models.clinical import Encounter
from app.models.enums import AlertKind, AlertStatus, AuditAction, RoleCode
from app.models.identity import User
from app.models.patient import Patient
from app.services import cleaning, patient_access, recipients, trail
from app.services.errors import ConflictError, ForbiddenError, InvalidValueError, NotFoundError, commit

logger = logging.getLogger(__name__)

ENTITY = "alerts"
EVENT_CRITICAL = "alerta_critica"
EVENT_BED = "alerta_cama_sin_limpiar"
EVENT_ACKNOWLEDGED = "alerta_reconocida"
EVENT_ESCALATED = "alerta_escalada"

# Probability at or above which a critical case is ``critico``; between the
# model threshold (it flagged the case) and this value the level is ``alto``.
CRITICAL_LEVEL_PROBABILITY = 0.80
LEVEL_CRITICAL = "critico"
LEVEL_HIGH = "alto"
MAX_LIST = 200

AUTO_RESOLVED_NOTE = (
    "Resuelta automaticamente: la limpieza fue aprobada o la cama cambio de estado"
)

# Roles that may read alerts. Anybody else answers 403.
VIEWER_ROLES = (
    RoleCode.ADMIN,
    RoleCode.EPS_COORDINATOR,
    RoleCode.IPS_CLINICAL_OPERATOR,
    RoleCode.SPECIALIST,
)


def _as_utc(moment: datetime) -> datetime:
    # SQLite drops the zone; a naive value in these columns is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def _patient_is_deleted() -> ColumnElement[bool]:
    """Correlated EXISTS: the alert's patient is soft-deleted."""

    return (
        select(Patient.id)
        .where(Patient.id == Alert.patient_id, Patient.deleted_at.is_not(None))
        .exists()
    )


def visibility_clause(user: User) -> ColumnElement[bool] | None:
    """WHERE fragment with the alerts the user may see; ``None`` = all of them."""

    role = user.role.code
    if role == RoleCode.ADMIN:
        return None
    if role == RoleCode.EPS_COORDINATOR:
        return Alert.kind == AlertKind.BED_NOT_CLEANED
    if role not in (RoleCode.IPS_CLINICAL_OPERATOR, RoleCode.SPECIALIST):
        raise ForbiddenError("Su rol no consulta alertas clinicas")

    about_my_patients: ColumnElement[bool] = Alert.recipient_user_id == user.id
    if user.organization_id is not None:
        about_my_patients = or_(
            about_my_patients,
            Alert.patient_id.in_(
                select(Encounter.patient_id).where(
                    Encounter.organization_id == user.organization_id,
                    Encounter.deleted_at.is_(None),
                )
            ),
        )
    granted = patient_access.referred_patient_ids(user)
    if granted is not None:
        about_my_patients = or_(about_my_patients, Alert.patient_id.in_(granted))

    critical = and_(Alert.kind == AlertKind.CRITICAL_PATIENT, about_my_patients)
    if role == RoleCode.IPS_CLINICAL_OPERATOR and user.organization_id is not None:
        return or_(
            critical,
            and_(
                Alert.kind == AlertKind.BED_NOT_CLEANED,
                Alert.organization_id == user.organization_id,
            ),
        )
    return critical


def alerts_stmt(user: User, *, estado: AlertStatus | None = None) -> Select[tuple[Alert]]:
    stmt = (
        select(Alert)
        .options(joinedload(Alert.acknowledger))
        .where(~_patient_is_deleted())
    )
    clause = visibility_clause(user)
    if clause is not None:
        stmt = stmt.where(clause)
    if estado is not None:
        stmt = stmt.where(Alert.status == estado)
    return stmt


def active_count_stmt(user: User) -> Select[tuple[int]]:
    """``COUNT`` of the active alerts the user can see (R18 counter)."""

    stmt = (
        select(func.count())
        .select_from(Alert)
        .where(~_patient_is_deleted(), Alert.status == AlertStatus.ACTIVE)
    )
    clause = visibility_clause(user)
    if clause is not None:
        stmt = stmt.where(clause)
    return stmt


def count_active(db: Session, user: User) -> int:
    if user.role.code not in VIEWER_ROLES:
        return 0
    return db.scalar(active_count_stmt(user)) or 0


def list_alerts(
    db: Session, user: User, *, estado: AlertStatus | None = None
) -> list[Alert]:
    stmt = alerts_stmt(user, estado=estado)
    return list(
        db.scalars(stmt.order_by(Alert.created_at.desc(), Alert.id).limit(MAX_LIST)).unique()
    )


def get_alert(db: Session, alert_id: uuid.UUID) -> Alert:
    alert = db.scalar(
        select(Alert)
        .options(joinedload(Alert.acknowledger))
        .where(Alert.id == alert_id)
        .execution_options(populate_existing=True)
    )
    if alert is None:
        raise NotFoundError("Alerta no encontrada")
    return alert


def get_visible_alert(db: Session, user: User, alert_id: uuid.UUID) -> Alert:
    """The alert, or 404 when it does not exist OR the user cannot see it."""

    clause = visibility_clause(user)
    stmt = select(Alert.id).where(Alert.id == alert_id, ~_patient_is_deleted())
    if clause is not None:
        stmt = stmt.where(clause)
    if db.scalar(stmt) is None:
        raise NotFoundError("Alerta no encontrada")
    return get_alert(db, alert_id)


# ---------------------------------------------------------------------------
# Audiences
# ---------------------------------------------------------------------------


def audience(db: Session, alert: Alert) -> tuple[uuid.UUID, ...]:
    """Everybody whose screen shows (and whose counter counts) this alert."""

    people: list[uuid.UUID] = list(db.scalars(recipients.admins_stmt()))
    if alert.kind == AlertKind.BED_NOT_CLEANED:
        people += db.scalars(recipients.coordinators_stmt())
        if alert.organization_id is not None:
            people += db.scalars(recipients.operators_of_organization_stmt(alert.organization_id))
    else:
        if alert.patient_id is not None:
            people += recipients.audience_for_patient(db, alert.patient_id)
        if alert.recipient_user_id is not None:
            people.append(alert.recipient_user_id)
    return tuple(dict.fromkeys(people))


def _event_datos(alerts: list[Alert], **extra: Any) -> dict[str, Any]:
    first = alerts[0]
    return {
        "alerta_id": str(first.id),
        "alerta_ids": [str(a.id) for a in alerts],
        "kind": first.kind.value,
        "nivel": first.nivel,
        **extra,
    }


# ---------------------------------------------------------------------------
# Critical patient (reactor of ``reporte_generado``)
# ---------------------------------------------------------------------------


def _level_for(probability: float) -> str:
    return LEVEL_CRITICAL if probability >= CRITICAL_LEVEL_PROBABILITY else LEVEL_HIGH


def _critical_message(patient: Patient, probability: float) -> str:
    return (
        f"Alerta critica: {patient.first_name} {patient.last_name} fue clasificado "
        f"como critico por el modelo (probabilidad {probability:.0%}). "
        "Revise el reporte y reconozca la alerta."
    )


def active_critical_alert(db: Session, patient_id: uuid.UUID) -> Alert | None:
    return db.scalar(
        select(Alert).where(
            Alert.patient_id == patient_id,
            Alert.kind == AlertKind.CRITICAL_PATIENT,
            Alert.status == AlertStatus.ACTIVE,
        )
    )


def raise_critical_alert(db: Session, report_id: uuid.UUID) -> tuple[Alert, bool] | None:
    """Create (or refresh) the active alert for a critical AI report.

    Returns ``(alert, created)``, or ``None`` when the report is not critical.
    ``created`` is False when an active alert already existed: the report is
    attached to it and nobody is notified a second time.
    """

    report = db.get(AiReport, report_id)
    if report is None or not report.is_critical:
        return None
    patient = db.get(Patient, report.patient_id)
    if patient is None or patient.deleted_at is not None:
        return None

    probability = float(report.probability)
    message = _critical_message(patient, probability)
    level = _level_for(probability)
    datos = {"probabilidad": probability, "modelo": report.model_version}

    existing = active_critical_alert(db, patient.id)
    if existing is not None:
        existing.ai_report_id = report.id
        existing.nivel = level
        existing.mensaje = message
        existing.datos = datos
        commit(db)
        return existing, False

    responsible = patient.responsible_clinician_id
    if responsible is not None and not recipients.is_usable_clinician(db, responsible):
        responsible = None
    alert = Alert(
        kind=AlertKind.CRITICAL_PATIENT,
        nivel=level,
        status=AlertStatus.ACTIVE,
        mensaje=message,
        patient_id=patient.id,
        ai_report_id=report.id,
        organization_id=db.scalar(recipients.latest_encounter_organization_stmt(patient.id)),
        recipient_user_id=responsible,
        created_at=datetime.now(timezone.utc),
        datos=datos,
    )
    db.add(alert)
    try:
        db.flush()
    except IntegrityError:
        # Two analyses raced for the same patient: the unique index kept one.
        db.rollback()
        winner = active_critical_alert(db, patient.id)
        if winner is None:
            raise
        return winner, False
    trail.audit(
        db,
        action=AuditAction.ALERT_CREATED,
        entity_type=ENTITY,
        entity_id=alert.id,
        actor=None,
        metadata={
            "alert_id": str(alert.id),
            "kind": alert.kind.value,
            "patient_id": str(patient.id),
            "report_id": str(report.id),
            "nivel": level,
            "probability": probability,
        },
    )
    commit(db)
    return alert, True


def announce_critical(db: Session, alert: Alert) -> None:
    patient = db.get(Patient, alert.patient_id) if alert.patient_id else None
    to = recipients.for_patient(db, patient) if patient is not None else ()
    if alert.recipient_user_id is not None and alert.recipient_user_id not in to:
        to = (alert.recipient_user_id, *to)
    publish(
        DomainEvent(
            tipo=EVENT_CRITICAL,
            mensaje=alert.mensaje,
            recipients=tuple(to),
            paciente_id=alert.patient_id,
            datos=_event_datos(
                [alert],
                reporte_id=str(alert.ai_report_id) if alert.ai_report_id else None,
                **(alert.datos or {}),
            ),
        ),
        db,
    )


# ---------------------------------------------------------------------------
# Acknowledge
# ---------------------------------------------------------------------------


def ensure_can_acknowledge(db: Session, user: User, alert: Alert) -> None:
    role = user.role.code
    if alert.kind == AlertKind.BED_NOT_CLEANED:
        if role in (RoleCode.ADMIN, RoleCode.EPS_COORDINATOR):
            return
        if role == RoleCode.IPS_CLINICAL_OPERATOR and user.organization_id == alert.organization_id:
            return
        raise ForbiddenError("Su rol no puede reconocer esta alerta de cama")

    if role not in authz.AI_REVIEWERS:
        raise ForbiddenError(
            "Solo un medico o un especialista reconoce una alerta clinica "
            "(el administrador la consulta, pero no la reconoce)"
        )
    if alert.recipient_user_id == user.id:
        return
    if alert.patient_id is None:
        raise ForbiddenError("La alerta no esta a su alcance")
    patient_access.ensure_staff_patient_scope(db, user, alert.patient_id)


def acknowledge(db: Session, alert: Alert, *, actor: User, nota: str) -> Alert:
    note = (nota or "").strip()
    if not note:
        raise InvalidValueError("Reconocer una alerta exige una nota")
    ensure_can_acknowledge(db, actor, alert)
    if alert.status != AlertStatus.ACTIVE:
        raise ConflictError("La alerta ya fue reconocida")

    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = actor.id
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.nota_reconocimiento = note
    trail.audit(
        db,
        action=AuditAction.ALERT_ACKNOWLEDGED,
        entity_type=ENTITY,
        entity_id=alert.id,
        actor=actor,
        metadata=_audit_meta(alert, nota=note),
    )
    commit(db)
    _announce_acknowledged(db, alert, by=actor.full_name)
    return get_alert(db, alert.id)


def _audit_meta(alert: Alert, **extra: Any) -> dict[str, Any]:
    return {
        "alert_id": str(alert.id),
        "kind": alert.kind.value,
        "patient_id": str(alert.patient_id) if alert.patient_id else None,
        "report_id": str(alert.ai_report_id) if alert.ai_report_id else None,
        "bed_id": str(alert.bed_id) if alert.bed_id else None,
        **extra,
    }


def _announce_acknowledged(db: Session, alert: Alert, *, by: str | None) -> None:
    # Live only: it exists so the other open screens drop the banner. The inbox
    # keeps the original alert; the alert itself remembers who acknowledged it.
    who = f"por {by}" if by else "automaticamente"
    publish(
        DomainEvent(
            tipo=EVENT_ACKNOWLEDGED,
            mensaje=f"Alerta reconocida {who}",
            recipients=audience(db, alert),
            paciente_id=alert.patient_id,
            datos=_event_datos([alert], estado=AlertStatus.ACKNOWLEDGED.value),
            persist=False,
        ),
        db,
    )


# ---------------------------------------------------------------------------
# Escalation (R20.4)
# ---------------------------------------------------------------------------


def _escalation_targets(db: Session, alert: Alert) -> tuple[uuid.UUID, ...]:
    """Admins plus the OTHER clinicians of the alert's IPS (never the ones already told)."""

    people: list[uuid.UUID] = list(db.scalars(recipients.admins_stmt()))
    if alert.organization_id is not None:
        people += db.scalars(recipients.clinicians_of_organization_stmt(alert.organization_id))
    elif alert.patient_id is not None:
        people += recipients.audience_for_patient(db, alert.patient_id)

    already: set[uuid.UUID] = set()
    if alert.kind == AlertKind.BED_NOT_CLEANED:
        already.update(db.scalars(recipients.coordinators_stmt()))
        if alert.organization_id is not None:
            already.update(db.scalars(recipients.operators_of_organization_stmt(alert.organization_id)))
    elif alert.recipient_user_id is not None:
        already.add(alert.recipient_user_id)
    return tuple(p for p in dict.fromkeys(people) if p not in already)


def overdue_unacknowledged_stmt(cutoff: datetime) -> Select[tuple[Alert]]:
    """Active alerts never escalated and older than ``cutoff``."""

    return (
        select(Alert)
        .where(
            Alert.status == AlertStatus.ACTIVE,
            Alert.escalated_at.is_(None),
            Alert.created_at <= cutoff,
            ~_patient_is_deleted(),
        )
        .order_by(Alert.created_at, Alert.id)
    )


def escalate_overdue(db: Session, *, now: datetime, minutes: int) -> list[Alert]:
    """Escalate every alert that has been ``activa`` for ``minutes`` or more.

    Idempotent: ``escalated_at`` is set on the first escalation and the alert is
    never selected again. A time-based rule over OUR OWN state, not a poll of an
    external system. ``now`` is a parameter so a test can freeze the clock.
    """

    cutoff = _as_utc(now) - timedelta(minutes=minutes)
    pending = list(db.scalars(overdue_unacknowledged_stmt(cutoff)))
    escalated: list[Alert] = []
    for alert in pending:
        targets = _escalation_targets(db, alert)
        alert.escalated_at = _as_utc(now)
        alert.escalated_to = [str(t) for t in targets]
        trail.audit(
            db,
            action=AuditAction.ALERT_ESCALATED,
            entity_type=ENTITY,
            entity_id=alert.id,
            actor=None,
            metadata=_audit_meta(
                alert, escalated_to=alert.escalated_to, minutes=minutes
            ),
        )
        commit(db)
        escalated.append(alert)

    _announce_escalations(db, escalated, minutes)
    return escalated


def _announce_escalations(db: Session, escalated: list[Alert], minutes: int) -> None:
    beds_by_org: dict[uuid.UUID | None, list[Alert]] = {}
    for alert in escalated:
        if alert.kind == AlertKind.BED_NOT_CLEANED:
            beds_by_org.setdefault(alert.organization_id, []).append(alert)
            continue
        publish(
            DomainEvent(
                tipo=EVENT_ESCALATED,
                mensaje=f"Alerta sin atender hace {minutes} min: {alert.mensaje}",
                recipients=tuple(uuid.UUID(t) for t in alert.escalated_to or []),
                paciente_id=alert.patient_id,
                datos=_event_datos([alert], minutos=minutes),
            ),
            db,
        )
    for group in beds_by_org.values():
        targets: dict[str, None] = {}
        for alert in group:
            targets.update(dict.fromkeys(alert.escalated_to or []))
        count = len(group)
        message = (
            f"Cama libre sin limpiar hace mas de {minutes} min: {group[0].mensaje}"
            if count == 1
            else f"{count} camas libres siguen sin limpiar hace mas de {minutes} min"
        )
        publish(
            DomainEvent(
                tipo=EVENT_ESCALATED,
                mensaje=message,
                recipients=tuple(uuid.UUID(t) for t in targets),
                datos=_event_datos(group, minutos=minutes),
            ),
            db,
        )


# ---------------------------------------------------------------------------
# Free but not cleaned (the professor's bed alert)
# ---------------------------------------------------------------------------


def active_bed_alerts_stmt() -> Select[tuple[Alert]]:
    return select(Alert).where(
        Alert.kind == AlertKind.BED_NOT_CLEANED, Alert.status == AlertStatus.ACTIVE
    )


def sync_bed_alerts(db: Session, *, now: datetime, minutes: int) -> dict[str, int]:
    """Raise an alert per overdue bed and close the ones that stopped being so.

    "Overdue" is the logic of ``GET /beds/cleaning/overdue``: the bed is in
    CLEANING with no approved cleaning evidence. It becomes an alert after
    ``minutes`` in that state. An active alert is closed automatically as soon as
    its bed is no longer overdue (cleaning approved, bed released or deleted).
    """

    overdue = cleaning.beds_cleaning_overdue(db, 0, now=now)
    overdue_ids = {item["location_id"] for item in overdue}
    active = {a.bed_id: a for a in db.scalars(active_bed_alerts_stmt())}

    resolved = [a for bed_id, a in active.items() if bed_id not in overdue_ids]
    for alert in resolved:
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = _as_utc(now)
        alert.nota_reconocimiento = AUTO_RESOLVED_NOTE
        trail.audit(
            db,
            action=AuditAction.ALERT_ACKNOWLEDGED,
            entity_type=ENTITY,
            entity_id=alert.id,
            actor=None,
            metadata=_audit_meta(alert, auto=True, nota=AUTO_RESOLVED_NOTE),
        )
        commit(db)
        _announce_acknowledged(db, alert, by=None)

    created: list[Alert] = []
    for item in overdue:
        if int(item["minutes_in_cleaning"]) < minutes or item["location_id"] in active:
            continue
        waited = int(item["minutes_in_cleaning"])
        alert = Alert(
            kind=AlertKind.BED_NOT_CLEANED,
            nivel=LEVEL_HIGH,
            status=AlertStatus.ACTIVE,
            mensaje=(
                f"La cama {item['code']} ({item['service'] or 'sin servicio'}) esta libre, "
                f"pero no ha sido limpiada: esta cama esta perdiendo tiempo ({waited} min)."
            ),
            bed_id=item["location_id"],
            organization_id=item["organization_id"],
            created_at=_as_utc(now),
            datos={"cama": item["code"], "minutos_en_limpieza": waited},
        )
        db.add(alert)
        db.flush()
        trail.audit(
            db,
            action=AuditAction.ALERT_CREATED,
            entity_type=ENTITY,
            entity_id=alert.id,
            actor=None,
            metadata=_audit_meta(alert, bed_code=item["code"], minutes=waited),
        )
        commit(db)
        created.append(alert)

    _announce_bed_alerts(db, created)
    return {"creadas": len(created), "resueltas": len(resolved)}


def _announce_bed_alerts(db: Session, created: list[Alert]) -> None:
    """One event per IPS, however many beds it covers: a backlog must not flood inboxes."""

    by_org: dict[uuid.UUID | None, list[Alert]] = {}
    for alert in created:
        by_org.setdefault(alert.organization_id, []).append(alert)
    for organization_id, group in by_org.items():
        to = list(db.scalars(recipients.coordinators_stmt()))
        if organization_id is not None:
            to += db.scalars(recipients.operators_of_organization_stmt(organization_id))
        count = len(group)
        message = (
            group[0].mensaje
            if count == 1
            else f"{count} camas estan libres, pero no han sido limpiadas: estan perdiendo tiempo."
        )
        publish(
            DomainEvent(
                tipo=EVENT_BED,
                mensaje=message,
                recipients=tuple(dict.fromkeys(to)),
                datos=_event_datos(group, cantidad=count),
            ),
            db,
        )
