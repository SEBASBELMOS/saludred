"""Automatic AI analysis: the criticality report appears without anyone asking.

Two triggers, one worker (:func:`analyze_encounter`):

1. **Event.** ``observacion_registrada`` (a vital sign saved through the API) and
   ``fhir_observacion`` (one that arrived from the FHIR server) reach
   :func:`app.events.flows.ai_auto_reactor`, which calls :func:`dispatch`. When
   the six core vitals of the encounter are all recorded, the analysis runs.
   Observations arrive one by one, so the first five find the set incomplete and
   do nothing.
2. **Fallback.** :func:`sweep`, called by every tick of the alert monitor,
   analyzes the open encounters that have been quiet for ``AI_AUTO_GRACE_SECONDS``
   with at least the minimum usable variables, using whatever is there. The
   imputed variables travel in the report, as in any other.

Rules shared by both:

* **Never blocks, never breaks the clinical write.** The work runs in a worker
  thread with its own database session (same idea as ``fhir_autosync``); every
  error is logged and swallowed.
* **Nothing half-written.** The AI service is called first; if it is down
  nothing is stored and the next observation or monitor tick tries again.
* **One automatic report per encounter and kind**, guaranteed by a partial
  UNIQUE index (``uq_ai_reports_auto_per_encounter``) plus an in-process claim.
  The loser of a race backs off silently.
* **One pending report per identical input across both paths.** The worker and
  the manual route share a per-patient lock (:mod:`app.services.patient_locks`)
  around "look up, call the model, store", and re-check the database inside it.
* **Attribution.** ``requested_by`` is null (the system asked), the origin is
  ``automatico`` and the audit entry carries ``origen: automatico``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import get_settings
from app.events.bus import DomainEvent, publish
from app.models.ai import AiReport
from app.models.clinical import Encounter, Observation
from app.models.enums import (
    AiReportKind,
    AiReportOrigin,
    EncounterStatus,
    ObservationStatus,
)
from app.models.patient import Patient
from app.services import ai_reports, ml_client, patient_locks, pseudonymization, recipients
from app.services.errors import (
    ConflictError,
    MlServiceRejectedError,
    MlServiceUnavailableError,
)

logger = logging.getLogger(__name__)

OPEN_ENCOUNTER_STATUSES = (EncounterStatus.IN_PROGRESS, EncounterStatus.ARRIVED)
NEEDS_SIGNATURE_EVENT = "analisis_ia_pendiente"
MAX_PER_SWEEP = 10  # AI calls per monitor tick: a backlog drains over several ticks

# Test seams. In production both stay at their defaults.
_session_factory: Callable[[], Session] | None = None
_inline = False

_executor: ThreadPoolExecutor | None = None
_claimed: set[uuid.UUID] = set()
_guard = threading.Lock()


def configure(*, session_factory: Callable[[], Session] | None = None, inline: bool = False) -> None:
    """Point the worker at another session factory and/or run it in the caller's thread."""

    global _session_factory, _inline
    _session_factory = session_factory
    _inline = inline


def enabled() -> bool:
    return get_settings().ai_auto_analysis


# ---------------------------------------------------------------------------
# Statements (kept separate so they can be compiled for PostgreSQL in tests)
# ---------------------------------------------------------------------------


def due_encounters_stmt(
    *, cutoff: datetime, since: datetime, limit: int = MAX_PER_SWEEP
) -> Select[tuple[uuid.UUID]]:
    """Open encounters without an automatic report whose last usable variable is
    older than ``cutoff`` (the grace period) but newer than ``since`` (the
    look-back window).

    An encounter is also skipped while its patient already has a criticality
    report (any origin or status) made after that last usable variable: the
    worker would find nothing new to analyze, and re-selecting it on every tick
    would let those encounters fill the ``LIMIT`` and starve the ones really due.
    A newer variable makes it due again.
    """

    has_report = (
        select(AiReport.id)
        .where(
            AiReport.encounter_id == Encounter.id,
            AiReport.kind == AiReportKind.CRITICALITY,
            AiReport.origin == AiReportOrigin.AUTOMATIC,
        )
        .exists()
    )
    last_usable = func.max(Observation.created_at)
    usable = (
        select(
            Encounter.id.label("encounter_id"),
            Encounter.patient_id.label("patient_id"),
            last_usable.label("last_usable"),
        )
        .join(Observation, Observation.encounter_id == Encounter.id)
        .join(Patient, Patient.id == Encounter.patient_id)
        .where(
            Encounter.deleted_at.is_(None),
            Encounter.ended_at.is_(None),
            Encounter.status.in_(OPEN_ENCOUNTER_STATUSES),
            Patient.deleted_at.is_(None),
            Observation.deleted_at.is_(None),
            Observation.status != ObservationStatus.CANCELLED,
            Observation.code.in_(pseudonymization.LOINC_TO_FIELD),
            Observation.value_numeric.is_not(None),
            ~has_report,
        )
        .group_by(Encounter.id, Encounter.patient_id)
        .having(last_usable <= cutoff, last_usable >= since)
        .subquery("usable")
    )
    covered = (
        select(AiReport.id)
        .where(
            AiReport.patient_id == usable.c.patient_id,
            AiReport.kind == AiReportKind.CRITICALITY,
            AiReport.created_at >= usable.c.last_usable,
        )
        .exists()
    )
    return (
        select(usable.c.encounter_id)
        .where(~covered)
        .order_by(usable.c.last_usable)
        .limit(limit)
    )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def missing_core(payload: dict[str, object]) -> list[str]:
    """Core vital fields the payload does not have yet."""

    return [name for name in pseudonymization.CORE_VITAL_FIELDS if name not in payload]


def _claim(encounter_id: uuid.UUID) -> bool:
    with _guard:
        if encounter_id in _claimed:
            return False
        _claimed.add(encounter_id)
        return True


def _release(encounter_id: uuid.UUID) -> None:
    with _guard:
        _claimed.discard(encounter_id)


@contextmanager
def _session() -> Iterator[Session]:
    db = (_session_factory or database.SessionLocal)()
    try:
        yield db
    finally:
        db.close()


def analyze_encounter(
    db: Session, encounter_id: uuid.UUID, *, allow_partial: bool = False
) -> AiReport | None:
    """Make the automatic report of one encounter if it is due. ``None`` otherwise.

    ``allow_partial`` is the fallback: analyze with the minimum usable variables
    instead of waiting for the six core vitals.
    """

    encounter = db.scalar(
        select(Encounter).where(Encounter.id == encounter_id, Encounter.deleted_at.is_(None))
    )
    if (
        encounter is None
        or encounter.ended_at is not None
        or encounter.status not in OPEN_ENCOUNTER_STATUSES
    ):
        return None
    patient = db.get(Patient, encounter.patient_id)
    if patient is None or patient.deleted_at is not None:
        return None

    # Same lock as the manual route: when both fire together, the second one
    # finds the first one's committed report and backs off (or returns it).
    with patient_locks.patient_lock(patient.id):
        report = _store_if_due(db, encounter, patient, allow_partial=allow_partial)
    if report is None:
        return None

    # The doctor hears first (one inbox row, one push): the alert and the counter
    # reactors behind ``reporte_generado`` make several round trips to the database.
    _tell_the_clinician(db, patient, report)
    ai_reports.announce_generated(db, report)
    return report


def _store_if_due(
    db: Session, encounter: Encounter, patient: Patient, *, allow_partial: bool
) -> AiReport | None:
    """The part of the analysis that must not interleave with another report
    of the same patient. Runs with the patient lock held."""

    if _has_automatic_report(db, encounter.id):
        return None

    payload = pseudonymization.build_criticality_payload(db, patient, encounter_id=encounter.id)
    if missing_core(payload) and not allow_partial:
        return None
    if ai_reports.count_clinical_inputs(payload) < ai_reports.MIN_CLINICAL_INPUTS:
        return None
    # A report with exactly this input already exists (a manual one waiting for a
    # signature, or one already signed or rejected): nothing new to analyze.
    if ai_reports.pending_with_payload(db, patient.id, payload, statuses=ai_reports.ANY_STATUS):
        return None

    # Network call first: if it fails nothing is written and a later
    # observation or monitor tick tries again.
    try:
        answer = ml_client.get_client().predict_criticality(payload)
    except (MlServiceUnavailableError, MlServiceRejectedError) as exc:
        logger.warning("Automatic AI analysis of encounter %s postponed: %s", encounter.id, exc)
        return None

    # No second look-up needed: the manual route waits on the same patient lock
    # for the whole model call, so nobody can store a report in the meantime.
    try:
        report = ai_reports.store_report(
            db,
            patient,
            payload=payload,
            answer=answer,
            actor=None,
            origin=AiReportOrigin.AUTOMATIC,
            encounter_id=encounter.id,
        )
    except ConflictError:
        # The partial UNIQUE index: another worker won the race. store_report
        # already rolled back, so nothing is half-written.
        logger.info("Encounter %s already has its automatic report", encounter.id)
        return None
    return report


def _has_automatic_report(db: Session, encounter_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(AiReport.id)
            .where(
                AiReport.encounter_id == encounter_id,
                AiReport.kind == AiReportKind.CRITICALITY,
                AiReport.origin == AiReportOrigin.AUTOMATIC,
            )
            .limit(1)
        )
        is not None
    )


def _tell_the_clinician(db: Session, patient: Patient, report: AiReport) -> None:
    """Inbox row plus live push for the responsible clinician (or the fallback).

    The event is clinical: the bus drops ``paciente`` and ``contable`` even if
    they were listed, so neither ever hears about it.
    """

    publish(
        DomainEvent(
            tipo=NEEDS_SIGNATURE_EVENT,
            mensaje=f"Nuevo análisis de IA pendiente de firma: {patient.full_name}",
            recipients=recipients.for_patient(db, patient),
            paciente_id=patient.id,
            datos={
                "reporte_id": str(report.id),
                "encounter_id": str(report.encounter_id) if report.encounter_id else None,
                "critico": report.is_critical,
                "origen": report.origin.value,
                "paciente_nombre": patient.full_name,
            },
        ),
        db,
    )


def run(encounter_id: uuid.UUID, *, allow_partial: bool = False) -> None:
    """Background entry point: own session, never raises."""

    if not _claim(encounter_id):
        return
    try:
        with _session() as db:
            analyze_encounter(db, encounter_id, allow_partial=allow_partial)
    except Exception:  # noqa: BLE001 -- background work must never propagate
        logger.exception("Automatic AI analysis of encounter %s crashed", encounter_id)
    finally:
        _release(encounter_id)


def dispatch(encounter_id: uuid.UUID) -> None:
    """Schedule the analysis of an encounter without blocking the caller."""

    if not enabled():
        return
    if _inline:
        run(encounter_id)
        return
    global _executor
    with _guard:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-auto")
        executor = _executor
    try:
        executor.submit(run, encounter_id)
    except RuntimeError:  # interpreter shutting down
        logger.warning("Automatic AI analysis of %s not scheduled: shutting down", encounter_id)


def sweep(db: Session, *, now: datetime) -> int:
    """Fallback for the monitor tick. Returns how many reports it made."""

    if not enabled():
        return 0
    settings = get_settings()
    ids = list(
        db.scalars(
            due_encounters_stmt(
                cutoff=now - timedelta(seconds=settings.ai_auto_grace_seconds),
                since=now - timedelta(minutes=settings.ai_auto_lookback_minutes),
            )
        )
    )
    made = 0
    for encounter_id in ids:
        if not _claim(encounter_id):
            continue
        try:
            if analyze_encounter(db, encounter_id, allow_partial=True) is not None:
                made += 1
        except Exception:  # noqa: BLE001 -- one encounter must not stop the others
            logger.exception("Fallback AI analysis of encounter %s failed", encounter_id)
            db.rollback()
        finally:
            _release(encounter_id)
    return made
