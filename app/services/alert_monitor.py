"""The alert monitor: an in-process timer over OUR OWN state (R20).

Every ``ALERT_MONITOR_INTERVAL_SECONDS`` (30 by default) it runs ``tick``:

1. escalates the alerts nobody acknowledged within ``ALERT_ESCALATION_MINUTES``
   (``alerts.escalate_overdue``);
2. raises the "free but not cleaned" alert for beds in CLEANING without approved
   evidence, and closes the ones that no longer are (``alerts.sync_bed_alerts``);
3. the fallback of the automatic AI analysis (``ai_auto.sweep``): open encounters
   whose vitals stayed incomplete past ``AI_AUTO_GRACE_SECONDS`` are analyzed with
   what they have, and an analysis the AI service could not make is retried.

It lives inside the FastAPI lifespan, which is valid because the API runs ONE
worker (``tests/test_deploy_constraints.py`` enforces it): with several workers
every one would run its own loop. This is not blind polling of an external
system: nothing asks HAPI or anybody else whether there is news. It is a
time-based rule over rows this system wrote itself, the same as a database job.

Robustness: each tick opens a FRESH session and closes it; each of the two jobs
is isolated, so a failure in one does not skip the other, and an exception never
kills the loop. Cancelling the task (shutdown) is the only way out.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import Settings
from app.services import ai_auto, alerts

logger = logging.getLogger(__name__)


def tick(
    db: Session, *, now: datetime, escalation_minutes: int, bed_minutes: int
) -> dict[str, int]:
    """One pass of the monitor over ``db``. ``now`` is a parameter so tests freeze the clock."""

    summary = {"escaladas": 0, "camas_creadas": 0, "camas_resueltas": 0, "ia_automaticos": 0}
    try:
        summary["escaladas"] = len(
            alerts.escalate_overdue(db, now=now, minutes=escalation_minutes)
        )
    except Exception:  # noqa: BLE001 -- one job must not stop the other
        logger.exception("Alert escalation failed")
        db.rollback()
    try:
        beds = alerts.sync_bed_alerts(db, now=now, minutes=bed_minutes)
        summary["camas_creadas"] = beds["creadas"]
        summary["camas_resueltas"] = beds["resueltas"]
    except Exception:  # noqa: BLE001
        logger.exception("Bed alert synchronization failed")
        db.rollback()
    try:
        # Fallback of the automatic AI analysis: encounters whose vitals stayed
        # incomplete past the grace period, and any analysis the AI service
        # could not make when the vitals were recorded (it is retried here).
        summary["ia_automaticos"] = ai_auto.sweep(db, now=now)
    except Exception:  # noqa: BLE001
        logger.exception("Automatic AI analysis sweep failed")
        db.rollback()
    if any(summary.values()):
        logger.info("Alert monitor: %s", summary)
    return summary


def run_once(
    settings: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Open a fresh session, run one tick, always close it."""

    factory = session_factory or database.SessionLocal
    db = factory()
    try:
        return tick(
            db,
            now=now or datetime.now(timezone.utc),
            escalation_minutes=settings.alert_escalation_minutes,
            bed_minutes=settings.bed_cleaning_alert_minutes,
        )
    finally:
        db.close()


async def monitor_loop(
    settings: Settings,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    """Run ``run_once`` forever, off the event loop (the work is synchronous SQL)."""

    logger.info(
        "Alert monitor started: every %ss, escalation after %s min, bed alert after %s min",
        settings.alert_monitor_interval_seconds,
        settings.alert_escalation_minutes,
        settings.bed_cleaning_alert_minutes,
    )
    while True:
        await asyncio.sleep(settings.alert_monitor_interval_seconds)
        try:
            await run_in_threadpool(run_once, settings, session_factory=session_factory)
        except Exception:  # noqa: BLE001 -- the loop must survive any error
            logger.exception("Alert monitor tick crashed")
