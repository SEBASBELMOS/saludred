"""Pending-work counters of a user (R18) and who must be told when they move.

``GET /api/v1/pendientes`` answers three numbers:

* ``reportes_por_revisar``: AI reports still ``pendiente`` inside the caller's
  scope. Only ``medico`` and ``especialista`` have any.
* ``remisiones_recibidas``: referrals ``pendiente`` addressed to the caller.
* ``alertas_activas``: active alerts addressed to, or visible for, the caller
  (see ``app.services.alerts.visibility_clause``).

Soft-deleted patients never count. Every query is a plain ``COUNT`` built by a
small function, so tests can compile it for PostgreSQL without a server.

The counters are pushed, not polled: after any event that changes one, the
``counters_reactor`` publishes a lightweight ``pendientes_actualizados`` event
to the affected users (see ``app.events.flows``).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core import authz
from app.events.bus import DomainEvent, publish
from app.events.channel import registry
from app.models.enums import ReferralStatus
from app.models.identity import User
from app.models.patient import Patient
from app.models.referral import Referral
from app.services import ai_reports, alerts

EVENT_COUNTERS = "pendientes_actualizados"


def referrals_received_stmt(user: User) -> Select[tuple[int]]:
    """Referrals still ``pendiente`` addressed to the user, patient not deleted."""

    return (
        select(func.count())
        .select_from(Referral)
        .join(Patient, Patient.id == Referral.patient_id)
        .where(
            Referral.specialist_user_id == user.id,
            Referral.status == ReferralStatus.PENDING,
            Patient.deleted_at.is_(None),
        )
    )


def counters(db: Session, user: User) -> dict[str, int]:
    # Only the clinicians who sign reports have reports to review.
    to_review = (
        db.scalar(ai_reports.pending_count_stmt(user)) or 0
        if user.role.code in authz.AI_REVIEWERS
        else 0
    )
    return {
        "reportes_por_revisar": to_review,
        "remisiones_recibidas": db.scalar(referrals_received_stmt(user)) or 0,
        "alertas_activas": alerts.count_active(db, user),
    }


# ---------------------------------------------------------------------------
# Telling the affected users that a counter moved
# ---------------------------------------------------------------------------


def publish_counters(
    db: Session,
    user_ids: tuple[uuid.UUID, ...] | list[uuid.UUID],
    *,
    motivo: str,
    paciente_id: uuid.UUID | None = None,
) -> None:
    """One ``pendientes_actualizados`` per affected user, live only.

    The event carries the fresh counters, so the front end repaints without
    asking again. It is never stored in the inbox (``persist=False``), and users
    with no open connection are skipped: nobody would receive it.
    """

    seen: set[uuid.UUID] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        if registry.connection_count(user_id) == 0:
            continue
        user = db.scalar(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )
        if user is None:
            continue
        data: dict[str, Any] = {**counters(db, user), "motivo": motivo}
        publish(
            DomainEvent(
                tipo=EVENT_COUNTERS,
                mensaje="Trabajo pendiente actualizado",
                recipients=(user.id,),
                paciente_id=paciente_id,
                datos=data,
                persist=False,
            ),
            db,
        )
