"""Domain-event bus (R19): business actions publish, small reactors react.

The business code (a hook handler today; referrals, report approval and account
locks in Epic 7) does not know who needs to be told. It builds a
:class:`DomainEvent`, commits its own transaction and calls :func:`publish`.
Reactors registered here do the rest: persist inbox rows, push to the live
channel, and in Epic 7 also bill or raise alerts.

Contract:

* ``publish`` runs AFTER the caller's commit, so a reactor can never observe
  (or leak) data that was rolled back.
* Reactors run synchronously, in registration order, on the caller's thread.
  One failing reactor is logged and isolated: it never raises into the caller
  and never prevents the next reactor from running.
* The bus is in-process. It is not a message broker; see ``docs/EVENTOS.md``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainEvent:
    """A fact that happened, addressed to concrete users.

    ``recipients`` are user ids resolved by the publisher: the bus has no
    opinion about who should care. ``clinical`` marks events that carry
    patient clinical data; those are never delivered to the ``paciente`` or
    ``contable`` roles, whoever is listed as recipient.

    ``persist=False`` marks a lightweight, live-only event (for example
    ``pendientes_actualizados``): it is pushed to the open connections but never
    written to the inbox, so it cannot flood it.
    """

    tipo: str
    mensaje: str
    recipients: tuple[uuid.UUID, ...]
    paciente_id: uuid.UUID | None = None
    datos: dict[str, Any] = field(default_factory=dict)
    clinical: bool = True
    persist: bool = True
    fecha: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Reactor = Callable[[DomainEvent, Session], None]

_reactors: list[Reactor] = []


def register_reactor(reactor: Reactor) -> None:
    """Add a reactor once; registering the same function twice is a no-op."""

    if reactor not in _reactors:
        _reactors.append(reactor)


def unregister_reactor(reactor: Reactor) -> None:
    if reactor in _reactors:
        _reactors.remove(reactor)


def registered_reactors() -> tuple[Reactor, ...]:
    return tuple(_reactors)


def publish(event: DomainEvent, db: Session) -> None:
    """Hand the event to every reactor, isolating failures.

    ``db`` is the caller's session, already committed. A reactor that fails
    leaves the session in an unknown state, so it is rolled back before the
    next reactor runs.
    """

    for reactor in tuple(_reactors):
        try:
            reactor(event, db)
        except Exception:  # noqa: BLE001 -- isolation is the whole point
            logger.exception(
                "Reactor %s failed for event %s", getattr(reactor, "__name__", reactor), event.tipo
            )
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("Rollback after a failed reactor also failed")
