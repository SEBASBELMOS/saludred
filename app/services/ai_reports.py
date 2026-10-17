"""AI reports with clinical sign-off (R10, "soft put").

The model proposes, a clinician decides:

1. A ``pendiente`` report holds the exact minimized, pseudonymized payload (R12)
   and the exact answer. It is created by ``app.services.ai_auto`` on its own
   (origin ``automatico``) or by ``request_analysis`` (origin ``manual``, and
   idempotent while an identical report is still pending). If the AI service
   fails nothing is written.
2. ``approve`` / ``reject`` are the clinician's signature. Only from
   ``pendiente`` (a second review is a 409). Reject needs a reason and keeps the
   report as evidence.
3. Only on approval does the AI touch operations: the approved probability
   becomes the criticality score of the patient's open bed request. Pending and
   rejected reports never reach the queue.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core import authz
from app.events.bus import DomainEvent, publish
from app.models.ai import AiReport
from app.models.beds import BedRequest
from app.models.clinical import Encounter
from app.core.config import get_settings
from app.models.enums import AiReportKind, AiReportOrigin, AiReportStatus, AuditAction
from app.models.identity import User
from app.models.patient import Patient
from app.schemas.ai import AiReportRead
from app.services import ml_client, patient_access, pseudonymization, trail
from app.services.beds import OPEN_STATUSES
from app.services.errors import (
    ConflictError,
    InvalidValueError,
    MlServiceRejectedError,
    NotFoundError,
    commit,
)

ENTITY = "ai_reports"
MIN_CLINICAL_INPUTS = 3  # same floor as the AI service
MAX_LIST = 100


def _audit_meta(report: AiReport, **extra: object) -> dict[str, Any]:
    return {
        "report_id": str(report.id),
        "patient_id": str(report.patient_id),
        "kind": report.kind.value,
        "pseudonym": report.pseudonym,
        "model_version": report.model_version,
        **extra,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_report(db: Session, report_id: uuid.UUID) -> AiReport:
    report = db.scalar(
        select(AiReport)
        .options(joinedload(AiReport.reviewer))
        .where(AiReport.id == report_id)
        # Re-read the row: after a review the cached reviewer would be stale.
        .execution_options(populate_existing=True)
    )
    if report is None:
        raise NotFoundError("Reporte de IA no encontrado")
    return report


def list_reports(
    db: Session,
    user: User,
    *,
    status: AiReportStatus | None = None,
    patient_id: uuid.UUID | None = None,
) -> list[AiReport]:
    stmt = reports_stmt(user, status=status, patient_id=patient_id)
    return list(db.scalars(stmt.order_by(AiReport.created_at.desc()).limit(MAX_LIST)))


def reports_stmt(
    user: User,
    *,
    status: AiReportStatus | None = None,
    patient_id: uuid.UUID | None = None,
) -> Select[tuple[AiReport]]:
    """AI reports in the caller's scope (own IPS, plus patients referred to a specialist)."""

    # A soft-deleted patient's reports stay as evidence but leave the work tray.
    stmt = (
        select(AiReport)
        .options(joinedload(AiReport.reviewer))
        .join(Patient, Patient.id == AiReport.patient_id)
        .where(Patient.deleted_at.is_(None))
    )
    return _scoped(stmt, user, status=status, patient_id=patient_id)


def pending_count_stmt(user: User) -> Select[tuple[int]]:
    """``COUNT`` of the pending reports in the caller's scope (R18 counter)."""

    stmt = (
        select(func.count())
        .select_from(AiReport)
        .join(Patient, Patient.id == AiReport.patient_id)
        .where(Patient.deleted_at.is_(None))
    )
    return _scoped(stmt, user, status=AiReportStatus.PENDING, patient_id=None)


def _scoped(stmt, user: User, *, status, patient_id):  # noqa: ANN001, ANN202
    scope = authz.org_filter_for(user)
    if scope is not None:
        in_scope = AiReport.patient_id.in_(
            select(Encounter.patient_id).where(
                Encounter.organization_id == scope, Encounter.deleted_at.is_(None)
            )
        )
        granted = patient_access.referred_patient_ids(user)
        if granted is not None:
            in_scope = or_(in_scope, AiReport.patient_id.in_(granted))
        stmt = stmt.where(in_scope)
    if status is not None:
        stmt = stmt.where(AiReport.status == status)
    if patient_id is not None:
        stmt = stmt.where(AiReport.patient_id == patient_id)
    return stmt


def to_read(report: AiReport) -> AiReportRead:
    response = report.model_response
    return AiReportRead(
        id=report.id,
        patient_id=report.patient_id,
        kind=report.kind,
        estado=report.status,
        pseudonym=report.pseudonym,
        probability=float(report.probability),
        nivel=report.level,
        critico=report.is_critical,
        threshold=response.get("threshold"),
        model_version=report.model_version,
        imputed=list(response.get("imputed", [])),
        top_features=list(response.get("top_features", [])),
        origen=report.origin,
        encounter_id=report.encounter_id,
        requested_by=report.requested_by,
        reviewed_by=report.reviewed_by,
        firmado_por=report.reviewer.full_name if report.reviewer is not None else None,
        reviewed_at=report.reviewed_at,
        observaciones=report.notes,
        motivo_rechazo=report.rejection_reason,
        solicitud_cama_id=report.applied_bed_request_id,
        created_at=report.created_at,
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def count_clinical_inputs(payload: dict[str, object]) -> int:
    return len([name for name in pseudonymization.CLINICAL_FIELDS if name in payload])


def pending_with_payload(
    db: Session, patient_id: uuid.UUID, payload: dict[str, object]
) -> AiReport | None:
    """A PENDING report of the patient whose stored payload equals ``payload``.

    Asking again for the same analysis changes nothing: there is already a
    report waiting for a signature that was made from exactly this input.
    """

    candidates = db.scalars(
        select(AiReport)
        .where(
            AiReport.patient_id == patient_id,
            AiReport.kind == AiReportKind.CRITICALITY,
            AiReport.status == AiReportStatus.PENDING,
        )
        .order_by(AiReport.created_at.desc())
        .limit(20)
    )
    for candidate in candidates:
        if candidate.payload_sent == payload:
            return candidate
    return None


def parse_answer(answer: dict[str, Any]) -> tuple[float, str, bool, str]:
    """``(probability, level, is_critical, version)`` of the model's answer."""

    try:
        return (
            float(answer["probability"]),
            str(answer["nivel"]),
            bool(answer["positive"]),
            str(answer["model_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MlServiceRejectedError("El servicio de IA devolvio una respuesta incompleta") from exc


def store_report(
    db: Session,
    patient: Patient,
    *,
    payload: dict[str, object],
    answer: dict[str, Any],
    actor: User | None,
    origin: AiReportOrigin,
    encounter_id: uuid.UUID | None = None,
) -> AiReport:
    """Persist a PENDING report, audit it and commit. Publishes nothing.

    ``actor`` is ``None`` for an automatic report: the system asked. The caller
    publishes ``reporte_generado`` after this returns, so reactors only see a
    committed report.
    """

    probability, level, is_critical, version = parse_answer(answer)
    report = AiReport(
        patient_id=patient.id,
        encounter_id=encounter_id,
        kind=AiReportKind.CRITICALITY,
        origin=origin,
        pseudonym=str(payload["pseudonym"]),
        payload_sent=payload,
        model_response=answer,
        model_version=version,
        probability=Decimal(str(round(probability, 4))),
        level=level,
        is_critical=is_critical,
        status=AiReportStatus.PENDING,
        requested_by=actor.id if actor is not None else None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(report)
    try:
        db.flush()
    except IntegrityError as exc:
        # The partial UNIQUE index (one automatic report per encounter): a
        # concurrent worker won the race. Nothing of ours stays half-written.
        db.rollback()
        raise ConflictError("Ya existe un reporte automatico para esta atencion") from exc
    trail.audit(
        db,
        action=AuditAction.AI_ANALYSIS,
        entity_type=ENTITY,
        entity_id=report.id,
        actor=actor,
        metadata=_audit_meta(
            report,
            probability=probability,
            nivel=level,
            origen=origin.value,
            encounter_id=str(encounter_id) if encounter_id else None,
        ),
    )
    commit(db)
    return report


def announce_generated(db: Session, report: AiReport) -> None:
    """After the commit: reactors raise the critical alert (R20) when the model
    says so, and move the "reports to review" counters (R18)."""

    publish(
        DomainEvent(
            tipo="reporte_generado",
            mensaje="Nuevo reporte de IA pendiente de revision",
            recipients=(),
            paciente_id=report.patient_id,
            datos={
                "reporte_id": str(report.id),
                "critico": report.is_critical,
                "nivel": report.level,
                "probabilidad": float(report.probability),
                "origen": report.origin.value,
            },
        ),
        db,
    )


def request_analysis(
    db: Session, patient: Patient, *, actor: User, triage_level: int | None = None
) -> AiReport:
    """Manual analysis (``POST /patients/{id}/ai-analyses``).

    Idempotent-friendly: when a PENDING report of the patient was made from a
    payload identical to the one that would be sent now, that report is returned
    and nothing is created (this also covers the automatic report that is
    already waiting). Anything else creates a new ``manual`` report.
    """

    payload = pseudonymization.build_criticality_payload(
        db,
        patient,
        acuity=triage_level,
        # A specialist referred this patient reads (and so analyses) the whole record.
        organization_id=patient_access.org_scope_for_patient(db, actor, patient.id),
    )
    given = count_clinical_inputs(payload)
    if given < MIN_CLINICAL_INPUTS:
        raise InvalidValueError(
            "El paciente no tiene suficientes datos clinicos para el analisis "
            f"(se necesitan al menos {MIN_CLINICAL_INPUTS} variables y hay {given})"
        )

    existing = pending_with_payload(db, patient.id, payload)
    if existing is not None:
        return get_report(db, existing.id)

    # Network call first: if it fails, nothing has been written.
    answer = ml_client.get_client().predict_criticality(payload)
    report = store_report(
        db, patient, payload=payload, answer=answer, actor=actor, origin=AiReportOrigin.MANUAL
    )
    announce_generated(db, report)
    return get_report(db, report.id)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _open_bed_request(db: Session, patient_id: uuid.UUID) -> BedRequest | None:
    return db.scalar(
        select(BedRequest)
        .join(Encounter, BedRequest.encounter_id == Encounter.id)
        .where(
            Encounter.patient_id == patient_id,
            BedRequest.status.in_(OPEN_STATUSES),
            BedRequest.deleted_at.is_(None),
        )
        .order_by(BedRequest.requested_at.desc())
        .limit(1)
    )


def _ensure_pending(report: AiReport) -> None:
    if report.status != AiReportStatus.PENDING:
        raise ConflictError(f"El reporte ya fue revisado ({report.status.value})")


MIN_REVIEW_NOTE = 10  # characters of clinical judgment, same floor as the web form


def _require_review_note(text: str | None, *, what: str) -> None:
    """Enforce the clinician's written concept when ``REQUIRE_REVIEW_NOTE`` is on."""

    if get_settings().require_review_note and len((text or "").strip()) < MIN_REVIEW_NOTE:
        raise InvalidValueError(
            f"{what} requiere el concepto clinico del profesional: "
            f"escriba al menos {MIN_REVIEW_NOTE} caracteres"
        )


def approve(
    db: Session, report: AiReport, *, actor: User, observaciones: str | None
) -> AiReport:
    _ensure_pending(report)
    _require_review_note(observaciones, what="Aprobar el reporte")
    report.status = AiReportStatus.APPROVED
    report.reviewed_by = actor.id
    report.reviewed_at = datetime.now(timezone.utc)
    report.notes = (observaciones or "").strip() or None

    applied: dict[str, object] = {}
    if report.kind == AiReportKind.CRITICALITY:
        request = _open_bed_request(db, report.patient_id)
        if request is not None:
            score = round(float(report.probability), 3)
            request.criticality_score = Decimal(str(score))
            report.applied_bed_request_id = request.id
            applied = {"bed_request_id": str(request.id), "criticality_score": score}

    trail.audit(
        db,
        action=AuditAction.REPORT_APPROVED,
        entity_type=ENTITY,
        entity_id=report.id,
        actor=actor,
        metadata=_audit_meta(report, **applied),
    )
    commit(db)
    # After the commit: reactors (billing, R19 flow 3) never see a rolled-back
    # approval. Nobody is addressed here; the billing reactor tells the
    # accountants with its own event.
    publish(
        DomainEvent(
            tipo="reporte_aprobado",
            mensaje="Reporte de IA aprobado",
            recipients=(),
            paciente_id=report.patient_id,
            datos={"reporte_id": str(report.id), "aprobado_por": str(actor.id)},
        ),
        db,
    )
    return get_report(db, report.id)


def reject(
    db: Session,
    report: AiReport,
    *,
    actor: User,
    motivo: str,
    observaciones: str | None,
) -> AiReport:
    _ensure_pending(report)
    _require_review_note(motivo, what="Rechazar el reporte")
    report.status = AiReportStatus.REJECTED
    report.reviewed_by = actor.id
    report.reviewed_at = datetime.now(timezone.utc)
    report.rejection_reason = motivo.strip()
    report.notes = (observaciones or "").strip() or None
    trail.audit(
        db,
        action=AuditAction.REPORT_REJECTED,
        entity_type=ENTITY,
        entity_id=report.id,
        actor=actor,
        metadata=_audit_meta(report, motivo=report.rejection_reason),
    )
    commit(db)
    # Nobody is addressed: the event only moves the "reports to review" counters.
    publish(
        DomainEvent(
            tipo="reporte_rechazado",
            mensaje="Reporte de IA rechazado",
            recipients=(),
            paciente_id=report.patient_id,
            datos={"reporte_id": str(report.id), "rechazado_por": str(actor.id)},
        ),
        db,
    )
    return get_report(db, report.id)


def approved_reports(db: Session, patient_id: uuid.UUID) -> list[AiReport]:
    return list(
        db.scalars(
            select(AiReport)
            .options(joinedload(AiReport.reviewer))
            .where(
                AiReport.patient_id == patient_id,
                AiReport.status == AiReportStatus.APPROVED,
            )
            .order_by(AiReport.reviewed_at.desc())
        )
    )
