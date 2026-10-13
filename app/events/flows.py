"""Reactors of the reactive system (Epic 7): critical alerts and pending counters.

Business code publishes facts (``reporte_generado``, ``remision_creada``...) and
knows nothing about alerts or counters; these small functions react to them:

* ``alert_reactor``    -- ``reporte_generado`` -> raise the critical alert (R20) and
  announce it (``alerta_critica``).
* ``counters_reactor`` -- any event that moves a pending-work counter (R18) ->
  one live ``pendientes_actualizados`` per affected user.

Inbox rows and live pushes are made by ``notification_reactor``; invoices by
``billing_reactor``. Services are imported inside the functions: ``app.events``
registers these reactors when it is imported, and the services import the bus.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.events.bus import DomainEvent

logger = logging.getLogger(__name__)

REPORT_GENERATED = "reporte_generado"

REPORT_EVENTS = frozenset({"reporte_generado", "reporte_aprobado", "reporte_rechazado"})
REFERRAL_EVENTS = frozenset(
    {"remision_creada", "remision_aceptada", "remision_rechazada", "remision_completada"}
)
ALERT_EVENTS = frozenset(
    {"alerta_critica", "alerta_cama_sin_limpiar", "alerta_reconocida", "alerta_escalada"}
)
COUNTER_EVENTS = REPORT_EVENTS | REFERRAL_EVENTS | ALERT_EVENTS


def alert_reactor(event: DomainEvent, db: Session) -> None:
    """A critical AI report raises (or refreshes) the patient's active alert."""

    if event.tipo != REPORT_GENERATED or not event.datos.get("critico"):
        return

    from app.services import alerts

    result = alerts.raise_critical_alert(db, uuid.UUID(event.datos["reporte_id"]))
    if result is None:
        return
    alert, created = result
    if created:
        alerts.announce_critical(db, alert)


def counters_reactor(event: DomainEvent, db: Session) -> None:
    """Tell the affected users that their pending-work counters moved."""

    if event.tipo not in COUNTER_EVENTS:
        return

    from app.models.alert import Alert
    from app.services import alerts, pending, recipients

    affected: set[uuid.UUID] = set()
    if event.tipo in REPORT_EVENTS and event.paciente_id is not None:
        affected.update(recipients.audience_for_patient(db, event.paciente_id))
    if event.tipo in REFERRAL_EVENTS:
        affected.update(event.recipients)
        for key in ("especialista_id", "remitente_id"):
            if event.datos.get(key):
                affected.add(uuid.UUID(event.datos[key]))
    if event.tipo in ALERT_EVENTS:
        for alert_id in event.datos.get("alerta_ids", []):
            alert = db.get(Alert, uuid.UUID(alert_id))
            if alert is not None:
                affected.update(alerts.audience(db, alert))
        affected.update(event.recipients)

    pending.publish_counters(db, sorted(affected, key=str), motivo=event.tipo, paciente_id=event.paciente_id)
