"""Automatic push to HAPI after a clinical write (R15).

The routes schedule :func:`run` with ``BackgroundTasks``: it executes AFTER the
response has been sent, with its own database session (the request's session is
closed by then). Two rules:

* A clinical write NEVER fails or slows down because HAPI is down. Every error
  is caught here, logged, and recorded in ``fhir_sync_log`` with status ERROR.
* Nothing is lost: ``POST /integration/fhir/retry-failed`` pushes again every
  patient that failed or never synchronized, and a later observation or
  encounter heals a missing dependency on its own.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core import database
from app.core.config import get_settings
from app.models.clinical import Encounter, Observation
from app.models.enums import ObservationOrigin, SyncStatus
from app.models.identity import User
from app.models.patient import Patient
from app.models.referral import Referral
from app.services import fhir_sync
from app.services.errors import FhirGatewayError, NotFoundError

logger = logging.getLogger(__name__)

KINDS: dict[str, tuple[type, str]] = {
    "patient": (Patient, "Patient"),
    "encounter": (Encounter, "Encounter"),
    "observation": (Observation, "Observation"),
    "referral": (Referral, "ServiceRequest"),
}

# Test seams: a session factory and a FHIR client factory. In production both
# are None and the real ones are used.
_session_factory: Callable[[], Session] | None = None
_client_factory: Callable[[], Any] | None = None


def configure(
    *,
    session_factory: Callable[[], Session] | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> None:
    global _session_factory, _client_factory
    _session_factory = session_factory
    _client_factory = client_factory


def enqueue(
    background: BackgroundTasks, kind: str, entity_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Schedule the push; a no-op when ``FHIR_AUTO_SYNC`` is off."""

    if not get_settings().fhir_auto_sync:
        return
    background.add_task(run, kind, entity_id, actor_id)


@contextmanager
def _session():  # noqa: ANN202
    db = (_session_factory or database.SessionLocal)()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def _client():  # noqa: ANN202
    if _client_factory is not None:
        yield _client_factory()
        return
    http = fhir_sync.new_fhir_client(
        timeout=get_settings().fhir_auto_sync_timeout_seconds
    )
    try:
        yield fhir_sync.FhirClient(http)
    finally:
        http.close()


def run(kind: str, entity_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    """Push one entity. Never raises."""

    try:
        model, resource_type = KINDS[kind]
        with _session() as db, _client() as client:
            actor = db.scalar(
                select(User).options(joinedload(User.role)).where(User.id == actor_id)
            )
            entity = db.get(model, entity_id)
            # A referral is never deleted, so it has no ``deleted_at``.
            if actor is None or entity is None or getattr(entity, "deleted_at", None) is not None:
                return
            if kind == "observation" and entity.origin != ObservationOrigin.LOCAL:
                return
            try:
                if kind == "patient":
                    fhir_sync.sync_patient(db, client, entity, actor=actor, deep=False)
                elif kind == "encounter":
                    fhir_sync.sync_encounter(db, client, entity, actor=actor, deep=False)
                elif kind == "referral":
                    fhir_sync.sync_referral(db, client, entity, actor=actor, deep=False)
                else:
                    fhir_sync.sync_observation(db, client, entity, actor=actor, deep=False)
            except (FhirGatewayError, NotFoundError) as exc:
                logger.warning("FHIR auto-sync of %s %s failed: %s", kind, entity_id, exc)
                db.rollback()
                record = fhir_sync.get_sync_record(db, entity.__tablename__, entity.id)
                if record is None or record.sync_status != SyncStatus.ERROR:
                    fhir_sync.record_failure(db, entity, resource_type, str(exc))
    except Exception:  # noqa: BLE001 -- a background task must never propagate
        logger.exception("FHIR auto-sync of %s %s crashed", kind, entity_id)
