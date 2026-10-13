"""Critical alerts (R20) and the pending-work counters (R18).

- ``GET /alerts``: ``medico`` and ``especialista`` see the alerts addressed to
  them or about patients in their scope; the coordinator the bed alerts; the
  admin all of them. ``paciente`` and ``contable``: 403 (they neither see nor
  acknowledge alerts).
- ``POST /alerts/{id}/acknowledge``: ``medico`` / ``especialista`` with access
  (the admin reads clinical alerts but never signs them). The note is required;
  a second acknowledgement is a 409. Bed alerts are also acknowledged by the
  coordinator, the admin and a medico of that IPS.
- ``GET /pendientes``: the three counters of the caller; every role answers 200.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import AlertStatus
from app.schemas.alert import AlertAcknowledge, AlertRead, PendingRead
from app.services import alerts as alerts_service
from app.services import pending

router = APIRouter(prefix="/api/v1", tags=["alertas"])


@router.get(
    "/alerts",
    response_model=list[AlertRead],
    summary="Alertas criticas (activas o reconocidas) al alcance del usuario",
)
def list_alerts(
    db: DbSession,
    user: CurrentUser,
    estado: AlertStatus | None = Query(default=None, description="activa | reconocida"),
) -> list[AlertRead]:
    authz.require_role(user, *alerts_service.VIEWER_ROLES)
    return [AlertRead.from_row(a) for a in alerts_service.list_alerts(db, user, estado=estado)]


@router.get("/alerts/{alert_id}", response_model=AlertRead, summary="Consultar una alerta")
def get_alert(db: DbSession, user: CurrentUser, alert_id: uuid.UUID) -> AlertRead:
    authz.require_role(user, *alerts_service.VIEWER_ROLES)
    return AlertRead.from_row(alerts_service.get_visible_alert(db, user, alert_id))


@router.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=AlertRead,
    summary="Reconocer una alerta con una nota (obligatoria)",
)
def acknowledge_alert(
    db: DbSession, user: CurrentUser, alert_id: uuid.UUID, payload: AlertAcknowledge
) -> AlertRead:
    authz.require_role(user, *alerts_service.VIEWER_ROLES)
    alert = alerts_service.get_visible_alert(db, user, alert_id)
    return AlertRead.from_row(alerts_service.acknowledge(db, alert, actor=user, nota=payload.nota))


@router.get(
    "/pendientes",
    response_model=PendingRead,
    summary="Contadores de trabajo pendiente del usuario",
)
def pendientes(db: DbSession, user: CurrentUser) -> PendingRead:
    return PendingRead(**pending.counters(db, user))
