"""Bed cleaning evidence: checklist, photo and human review.

The rule this module protects: a bed is not "clean" because someone says so,
but because a record with a complete checklist and a photo was approved by a
person other than the one who wrote it. Neither the uploader nor any automatic
process approves its own work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.beds import BedAssignment, BedStatusEvent
from app.models.cleaning import CHECKLIST_ITEMS, BedCleaningRecord
from app.models.enums import (
    AuditAction,
    BedAssignmentStatus,
    BedStatus,
    CleaningDisinfectant,
    CleaningIsolation,
    CleaningReviewStatus,
    LocationType,
)
from app.models.identity import User
from app.models.organization import Location
from app.schemas.cleaning import CleaningRecordCreate
from app.services import pacs, trail
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidUploadError,
    InvalidValueError,
    NotFoundError,
    commit,
)

MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg"}

ENTITY = "bed_cleaning_records"


def _as_utc(moment: datetime) -> datetime:
    # SQLite pierde la zona horaria; una fecha sin zona en estas columnas es UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


def get_record(db: Session, record_id: uuid.UUID) -> BedCleaningRecord:
    record = db.get(BedCleaningRecord, record_id)
    if record is None:
        raise NotFoundError("Registro de limpieza no encontrado")
    return record


def list_for_bed(db: Session, location_id: uuid.UUID) -> list[BedCleaningRecord]:
    """Records of one bed, newest first."""

    return list(
        db.scalars(
            select(BedCleaningRecord)
            .where(BedCleaningRecord.location_id == location_id)
            .order_by(BedCleaningRecord.created_at.desc())
        )
    )


def get_photo(db: Session, record: BedCleaningRecord) -> tuple[bytes, str]:
    if not record.has_photo:
        raise NotFoundError("El registro no tiene foto")
    # La columna es diferida: se carga solo cuando alguien pide la imagen.
    return bytes(record.photo), record.photo_content_type or "application/octet-stream"


# ---------------------------------------------------------------------------
# Creacion
# ---------------------------------------------------------------------------


def _validate_payload(data: CleaningRecordCreate) -> None:
    if _as_utc(data.finished_at) < _as_utc(data.started_at):
        raise InvalidValueError("La limpieza no puede terminar antes de empezar")

    unknown = sorted(set(data.checklist) - set(CHECKLIST_ITEMS))
    if unknown:
        raise InvalidValueError(f"Items de lista de chequeo desconocidos: {', '.join(unknown)}")
    incomplete = [key for key in CHECKLIST_ITEMS if not data.checklist.get(key, False)]
    if incomplete:
        raise InvalidValueError(
            f"La limpieza no esta completa: faltan {', '.join(incomplete)}"
        )

    if data.isolation != CleaningIsolation.NONE and not data.curtains_changed:
        raise InvalidValueError(
            "Una habitacion con aislamiento exige cambio de cortinas"
        )
    if (
        data.isolation == CleaningIsolation.SPORES
        and data.disinfectant != CleaningDisinfectant.SPORICIDAL
    ):
        raise InvalidValueError(
            "El aislamiento por esporas exige desinfectante esporicida"
        )


def create_record(
    db: Session, bed: Location, data: CleaningRecordCreate, *, actor: User
) -> BedCleaningRecord:
    """Register the terminal cleaning of a bed that is in CLEANING."""

    if bed.location_type != LocationType.BED:
        raise ConflictError("La ubicacion indicada no es una cama")
    if bed.status != BedStatus.CLEANING:
        raise ConflictError(
            "Solo se registra limpieza de camas en estado CLEANING "
            f"(estado actual {bed.status.value})"
        )
    _validate_payload(data)

    entry = entry_event(db, bed)
    record = BedCleaningRecord(
        location_id=bed.id,
        started_at=data.started_at,
        finished_at=data.finished_at,
        isolation=data.isolation,
        disinfectant=data.disinfectant,
        curtains_changed=data.curtains_changed,
        checklist={key: True for key in CHECKLIST_ITEMS},
        notes=data.notes,
        status=CleaningReviewStatus.PENDING_REVIEW,
        created_by=actor.id,
        predicted_delay_probability=entry.predicted_delay_probability if entry else None,
        predicted_delay=entry.predicted_delay if entry else None,
        delay_model_version=entry.delay_model_version if entry else None,
        # Explicito y con microsegundos: el orden frente a bed_status_events
        # decide si la evidencia es de esta limpieza o de una anterior.
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    trail.audit(
        db,
        action=AuditAction.CLEANING_EVIDENCE,
        entity_type=ENTITY,
        entity_id=record.id,
        actor=actor,
        metadata={"bed_id": str(bed.id), "record_id": str(record.id), "action": "created"},
    )
    commit(db)
    db.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Foto
# ---------------------------------------------------------------------------


def _validate_photo(data: bytes) -> str:
    """Byte-level checks; the PACS validator also accepts DICOM, this does not."""

    if not data:
        raise InvalidUploadError("El archivo esta vacio")
    if len(data) > MAX_PHOTO_BYTES:
        raise InvalidUploadError(
            f"El archivo supera el maximo de {MAX_PHOTO_BYTES // (1024 * 1024)} MB"
        )
    kind = pacs.sniff(data)
    if kind not in PHOTO_CONTENT_TYPES:
        raise InvalidUploadError("Solo se aceptan fotos PNG o JPEG")
    size = pacs.image_size(data, kind)
    if size is None:
        raise InvalidUploadError("No se pudieron leer las dimensiones de la imagen")
    if max(size) > pacs.MAX_DIMENSION or min(size) < 1:
        raise InvalidUploadError(
            f"La imagen debe medir como maximo {pacs.MAX_DIMENSION} px por lado"
        )
    return PHOTO_CONTENT_TYPES[kind]


def attach_photo(
    db: Session, record: BedCleaningRecord, data: bytes, *, actor: User
) -> BedCleaningRecord:
    if record.status != CleaningReviewStatus.PENDING_REVIEW:
        raise ConflictError("Solo se puede cambiar la foto mientras el registro esta en revision")
    content_type = _validate_photo(data)

    record.photo = data
    record.photo_content_type = content_type
    record.photo_size = len(data)
    trail.audit(
        db,
        action=AuditAction.CLEANING_EVIDENCE,
        entity_type=ENTITY,
        entity_id=record.id,
        actor=actor,
        metadata={
            "bed_id": str(record.location_id),
            "record_id": str(record.id),
            "action": "photo_uploaded",
        },
    )
    commit(db)
    db.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Revision
# ---------------------------------------------------------------------------


def review_record(
    db: Session,
    record: BedCleaningRecord,
    *,
    approved: bool,
    review_notes: str | None,
    actor: User,
) -> BedCleaningRecord:
    """Approve or reject a record. Never the person who wrote it."""

    if record.created_by == actor.id:
        raise ForbiddenError("Quien registra la limpieza no puede aprobarla")
    if record.status != CleaningReviewStatus.PENDING_REVIEW:
        raise ConflictError(f"El registro ya fue revisado ({record.status.value})")
    notes = (review_notes or "").strip() or None
    if approved and not record.has_photo:
        raise ConflictError("Falta la foto de evidencia")
    if not approved and notes is None:
        raise InvalidValueError("Rechazar un registro exige indicar el motivo")

    record.status = (
        CleaningReviewStatus.APPROVED if approved else CleaningReviewStatus.REJECTED
    )
    record.reviewed_by = actor.id
    record.reviewed_at = datetime.now(timezone.utc)
    record.review_notes = notes
    trail.audit(
        db,
        action=AuditAction.CLEANING_EVIDENCE,
        entity_type=ENTITY,
        entity_id=record.id,
        actor=actor,
        metadata={
            "bed_id": str(record.location_id),
            "record_id": str(record.id),
            "action": "approved" if approved else "rejected",
        },
    )
    commit(db)
    db.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Compuerta de disponibilidad y alerta
# ---------------------------------------------------------------------------


def entry_event(db: Session, bed: Location) -> BedStatusEvent | None:
    """The latest event that moved the bed into CLEANING, if there is one."""

    return db.scalar(
        select(BedStatusEvent)
        .where(
            BedStatusEvent.location_id == bed.id,
            BedStatusEvent.new_status == BedStatus.CLEANING,
        )
        .order_by(BedStatusEvent.event_at.desc())
        .limit(1)
    )


def cleaning_since(db: Session, bed: Location) -> datetime:
    """When the bed entered CLEANING.

    Prefers the latest status event into CLEANING. Without one (data loaded
    before the timeline existed) it falls back to the latest released
    assignment, and finally to the last update of the bed.
    """

    entered = db.scalar(
        select(BedStatusEvent.event_at)
        .where(
            BedStatusEvent.location_id == bed.id,
            BedStatusEvent.new_status == BedStatus.CLEANING,
        )
        .order_by(BedStatusEvent.event_at.desc())
        .limit(1)
    )
    if entered is None:
        entered = db.scalar(
            select(BedAssignment.released_at)
            .where(
                BedAssignment.location_id == bed.id,
                BedAssignment.status == BedAssignmentStatus.RELEASED,
                BedAssignment.released_at.is_not(None),
            )
            .order_by(BedAssignment.released_at.desc())
            .limit(1)
        )
    return _as_utc(entered if entered is not None else bed.updated_at)


def _records_since(
    db: Session, bed: Location, since: datetime
) -> list[BedCleaningRecord]:
    records = db.scalars(
        select(BedCleaningRecord).where(BedCleaningRecord.location_id == bed.id)
    )
    # Se compara en Python: SQLite devuelve fechas sin zona.
    return [r for r in records if _as_utc(r.created_at) >= since]


def has_approved_evidence(db: Session, bed: Location) -> bool:
    """Is there an APPROVED record made after the bed entered CLEANING?"""

    since = cleaning_since(db, bed)
    return any(
        r.status == CleaningReviewStatus.APPROVED for r in _records_since(db, bed, since)
    )


def beds_cleaning_overdue(
    db: Session, minutes: int, *, now: datetime | None = None
) -> list[dict[str, object]]:
    """Beds in CLEANING for over ``minutes`` with no approved evidence.

    This is the "free but not cleaned" condition: the patient left, the bed
    looks available to the naked eye, and nobody has proven it is safe to use.
    Read-only; the caller decides what to do with it (alert, escalate). ``now``
    lets a test freeze the clock; the alert monitor passes its own tick time.
    """

    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=minutes)
    beds = db.scalars(
        select(Location)
        .where(
            Location.location_type == LocationType.BED,
            Location.status == BedStatus.CLEANING,
            Location.deleted_at.is_(None),
        )
        .order_by(Location.code)
    )

    overdue: list[dict[str, object]] = []
    for bed in beds:
        since = cleaning_since(db, bed)
        if since > cutoff:
            continue
        current = _records_since(db, bed, since)
        if any(r.status == CleaningReviewStatus.APPROVED for r in current):
            continue
        entry = entry_event(db, bed)
        overdue.append(
            {
                "predicted_delay_probability": entry.predicted_delay_probability if entry else None,
                "predicted_delay": entry.predicted_delay if entry else None,
                "location_id": bed.id,
                "code": bed.code,
                "name": bed.name,
                "service": bed.service,
                "organization_id": bed.organization_id,
                "cleaning_since": since,
                "minutes_in_cleaning": int((now - since).total_seconds() // 60),
                "pending_review_records": sum(
                    1 for r in current if r.status == CleaningReviewStatus.PENDING_REVIEW
                ),
            }
        )
    overdue.sort(key=lambda item: item["cleaning_since"])
    return overdue
