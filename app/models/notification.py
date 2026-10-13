"""Per-user notification inbox (R18).

One row per recipient: the inbox is private, so a notification addressed to
three clinicians is three rows, each with its own read flag. The row is the
source of truth; the live channel (SSE) is only a best-effort push of the same
content, and a client that reconnects reads the inbox to catch up.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.governance import JSON_COLUMN


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Event type, e.g. "fhir_observacion". Free text on purpose: Epic 7 adds
    # types without a migration.
    kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Identifiers of the fact (observation id, FHIR resource, ...).
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Notification {self.kind} user={self.user_id}>"
