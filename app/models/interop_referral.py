"""Durable correlation and delivery evidence for the referral round trip."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.governance import JSON_COLUMN


class OutboundReferral(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "outbound_referrals"

    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interop_clients.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    service_request_id: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="enviada")
    reason: Mapped[str] = mapped_column(String(1000))
    service: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(20))
    response_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    response_task: Mapped[dict | None] = mapped_column(JSON_COLUMN, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PartnerDiagnosticReport(UUIDPrimaryKeyMixin, Base):
    """Partner evidence, kept separate from locally approved AI analyses."""

    __tablename__ = "partner_diagnostic_reports"

    referral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referrals.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    resource: Mapped[dict] = mapped_column(JSON_COLUMN)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InteropTaskOutbox(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "interop_task_outbox"

    referral_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("referrals.id"), unique=True)
    client_id: Mapped[str] = mapped_column(String(80))
    resource: Mapped[dict] = mapped_column(JSON_COLUMN)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
