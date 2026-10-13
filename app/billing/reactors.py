"""Event-bus reactors for billing (R19 flow 3).

* ``reporte_aprobado`` -> an ``analisis_ia`` invoice linked by ``reporte_id``.
* ``cama_liberada``    -> a ``dia_cama`` invoice linked to the bed request.
* ``remision_aceptada``-> a ``remision`` invoice linked by ``remision_id``.

All are idempotent, so a replayed event does not duplicate anything and does
not announce twice. Billing never breaks the business action that triggered it:
if Mongo is down the failure is logged and the invoice is left for
``POST /api/v1/facturas/reconciliar``.

The service is imported inside the function: ``app.events`` registers this
reactor when it is imported, and ``app.billing.service`` imports the bus.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.events.bus import DomainEvent
from app.services.errors import BillingUnavailableError

logger = logging.getLogger(__name__)

REPORT_APPROVED = "reporte_aprobado"
BED_RELEASED = "cama_liberada"
REFERRAL_ACCEPTED = "remision_aceptada"


def billing_reactor(event: DomainEvent, db: Session) -> None:
    if event.tipo not in (REPORT_APPROVED, BED_RELEASED, REFERRAL_ACCEPTED):
        return

    from app.billing import service

    try:
        if event.tipo == REPORT_APPROVED:
            billed = service.bill_report(db, uuid.UUID(event.datos["reporte_id"]))
        elif event.tipo == BED_RELEASED:
            billed = service.bill_bed_stay(db, uuid.UUID(event.datos["asignacion_id"]))
        else:
            if event.paciente_id is None:
                return
            ips = event.datos.get("ips_id")
            # ``bill_referral`` announces a new invoice by itself.
            service.bill_referral(
                db,
                patient_id=event.paciente_id,
                remision_id=uuid.UUID(event.datos["remision_id"]),
                ips_id=uuid.UUID(ips) if ips else None,
            )
            return
    except BillingUnavailableError:
        logger.warning(
            "Billing unavailable: %s left unbilled; run POST /api/v1/facturas/reconciliar",
            event.tipo,
        )
        return

    if billed is not None and billed.created:
        service.announce_created(db, billed.factura)
