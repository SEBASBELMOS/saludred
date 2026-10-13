"""Alerts that stay on screen until somebody deals with them (R20).

Two kinds share the table:

* ``paciente_critico``: the AI model classified a patient as critical. It is
  addressed to the responsible clinician (``recipient_user_id``) and stays
  visible to the clinicians in the patient's scope.
* ``cama_sin_limpiar``: a bed is free but its cleaning has not been approved.
  It has no patient; it belongs to an IPS (``organization_id``).

An alert is ``activa`` until it is acknowledged with a note. Partial unique
indexes keep at most one ACTIVE critical alert per patient and one ACTIVE bed
alert per bed, so a retry or a second analysis never duplicates the banner.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import AlertKind, AlertStatus, enum_column
from app.models.governance import JSON_COLUMN
from app.models.identity import User
from app.models.patient import Patient

_ACTIVE_CRITICAL = text("kind = 'paciente_critico' AND estado = 'activa'")
_ACTIVE_BED = text("kind = 'cama_sin_limpiar' AND estado = 'activa'")


class Alert(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_active_critical_patient",
            "patient_id",
            unique=True,
            postgresql_where=_ACTIVE_CRITICAL,
            sqlite_where=_ACTIVE_CRITICAL,
        ),
        Index(
            "uq_alerts_active_bed",
            "bed_id",
            unique=True,
            postgresql_where=_ACTIVE_BED,
            sqlite_where=_ACTIVE_BED,
        ),
        Index("ix_alerts_estado_created_at", "estado", "created_at"),
    )

    kind: Mapped[AlertKind] = mapped_column(enum_column(AlertKind, "alert_kind"), nullable=False)
    nivel: Mapped[str] = mapped_column(String(20), nullable=False)
    # Python attribute ``status``; the column keeps the contract name ``estado``.
    status: Mapped[AlertStatus] = mapped_column(
        "estado",
        enum_column(AlertStatus, "alert_status"),
        nullable=False,
        default=AlertStatus.ACTIVE,
    )
    mensaje: Mapped[str] = mapped_column(String(500), nullable=False)

    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("patients.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    ai_report_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_reports.id", ondelete="SET NULL"), nullable=True
    )
    bed_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"), nullable=True
    )
    # IPS the alert belongs to: where the bed is, or where the patient is cared for.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The responsible clinician. NULL for bed alerts and when the fallback
    # recipients were used.
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_to: Mapped[list[str] | None] = mapped_column(JSON_COLUMN, nullable=True)

    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nota_reconocimiento: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Extra facts of the alert (probability, bed code, minutes...), JSON.
    datos: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)

    patient: Mapped[Patient | None] = relationship(foreign_keys=[patient_id])
    acknowledger: Mapped[User | None] = relationship(foreign_keys=[acknowledged_by])

    def __repr__(self) -> str:
        return f"<Alert {self.kind} {self.status}>"
