"""AI reports awaiting, or carrying, a clinician's sign-off (R10).

A report is born ``pendiente`` and is not part of the clinical record. A doctor
or specialist approves or rejects it; only approved reports are exported to the
record. Nothing here is ever deleted: a rejected report is evidence of what the
model said and of the human who disagreed.

What the model received (``payload_sent``) and what it answered
(``model_response``) are written once. An ORM guard refuses to change them, so
the audit question "exactly what was sent?" always has a stable answer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import AiReportKind, AiReportOrigin, AiReportStatus, enum_column
from app.models.governance import JSON_COLUMN
from app.models.identity import User
from app.models.patient import Patient

IMMUTABLE_COLUMNS = (
    "patient_id",
    "kind",
    "pseudonym",
    "payload_sent",
    "model_response",
    "model_version",
    "probability",
    "level",
    "is_critical",
    "requested_by",
    "encounter_id",
    "origin",
    "created_at",
)


class AiReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_reports"
    __table_args__ = (
        # Exactly one automatic report per encounter and kind, whatever the
        # concurrency: the loser of a race gets an IntegrityError and backs off.
        Index(
            "uq_ai_reports_auto_per_encounter",
            "encounter_id",
            "kind",
            unique=True,
            postgresql_where=text("origin = 'automatico'"),
            sqlite_where=text("origin = 'automatico'"),
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[AiReportKind] = mapped_column(
        enum_column(AiReportKind, "ai_report_kind"), nullable=False
    )
    pseudonym: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload_sent: Mapped[dict[str, Any]] = mapped_column(JSON_COLUMN, nullable=False)
    model_response: Mapped[dict[str, Any]] = mapped_column(JSON_COLUMN, nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    probability: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    # Model verdict ("critico" / "no_critico"): the hook for critical alerts.
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)

    status: Mapped[AiReportStatus] = mapped_column(
        enum_column(AiReportStatus, "ai_report_status"),
        nullable=False,
        default=AiReportStatus.PENDING,
        index=True,
    )
    # Null for an automatic report: the system asked, not a person.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # Encounter the automatic analysis was made for (null for manual reports).
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("encounters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    origin: Mapped[AiReportOrigin] = mapped_column(
        enum_column(AiReportOrigin, "ai_report_origin"),
        nullable=False,
        default=AiReportOrigin.MANUAL,
        server_default=AiReportOrigin.MANUAL.value,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Clinician's own notes and, on rejection, the reason.
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Bed request whose criticality was set from this report when it was approved.
    applied_bed_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("bed_requests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])
    requester: Mapped[User | None] = relationship(foreign_keys=[requested_by])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])

    def __repr__(self) -> str:
        return f"<AiReport {self.id} {self.status}>"


@event.listens_for(AiReport, "before_update")
def _refuse_changes_to_evidence(_mapper: Mapper, _connection: object, target: AiReport) -> None:
    state = inspect(target)
    changed = [
        name for name in IMMUTABLE_COLUMNS if state.attrs[name].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "Un reporte de IA es inmutable: no se pueden modificar " + ", ".join(changed)
        )
