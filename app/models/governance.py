"""Traceability: edit history, audit trail and FHIR synchronization state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import AuditAction, SyncStatus, enum_column


class RecordVersion(UUIDPrimaryKeyMixin, Base):
    """A snapshot of a row taken immediately before it is modified.

    This is what makes the edit a *soft* edit: the previous values survive instead
    of being overwritten. One generic table serves every entity, so adding a new
    auditable entity costs nothing.
    """

    __tablename__ = "record_versions"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "version_number", name="uq_record_versions_entity_type_entity_id_version_number"
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_fields: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<RecordVersion {self.entity_type}:{self.entity_id} v{self.version_number}>"


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Who did what, to which record, and when.

    ``user_id`` is nullable so a failed login attempt, which has no authenticated
    user, can still be recorded.
    """

    __tablename__ = "audit_log"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    action: Mapped[AuditAction] = mapped_column(
        enum_column(AuditAction, "audit_action"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    user: Mapped["object | None"] = relationship("User", lazy="joined")

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.entity_type}>"


class FhirSyncLog(UUIDPrimaryKeyMixin, Base):
    """Link between a local row and its counterpart on the FHIR server.

    The unique constraint on ``(entity_type, entity_id)`` is what guarantees
    idempotency: one local record can only ever map to one FHIR resource, so
    running the synchronization twice updates instead of duplicating.
    """

    __tablename__ = "fhir_sync_log"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_fhir_sync_log_entity_type_entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    fhir_resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    fhir_resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fhir_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sync_status: Mapped[SyncStatus] = mapped_column(
        enum_column(SyncStatus, "fhir_sync_status"),
        nullable=False,
        default=SyncStatus.PENDING,
        index=True,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<FhirSyncLog {self.fhir_resource_type}:{self.fhir_resource_id}>"
