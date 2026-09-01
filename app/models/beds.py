"""Bed coordination: requests, assignments and the status timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import (
    BedAssignmentStatus,
    BedRequestStatus,
    BedStatus,
    Priority,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.clinical import Encounter
    from app.models.organization import Location, Organization


class BedRequest(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A request for a bed, raised by one IPS and routed through the EPS.

    ``target_organization_id`` stays nullable on purpose: a request starts without
    a destination and gets one when the EPS coordinator places it. That nullability
    is the whole point of the network scope.
    """

    __tablename__ = "bed_requests"

    encounter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("encounters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requesting_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    required_service: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[Priority] = mapped_column(
        enum_column(Priority, "bed_request_priority"), nullable=False
    )
    status: Mapped[BedRequestStatus] = mapped_column(
        enum_column(BedRequestStatus, "bed_request_status"),
        nullable=False,
        default=BedRequestStatus.PENDING,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    encounter: Mapped["Encounter"] = relationship(back_populates="bed_requests")
    requesting_organization: Mapped["Organization"] = relationship(
        foreign_keys=[requesting_organization_id]
    )
    target_organization: Mapped["Organization | None"] = relationship(
        foreign_keys=[target_organization_id]
    )
    assignments: Mapped[list["BedAssignment"]] = relationship(back_populates="bed_request")

    def __repr__(self) -> str:
        return f"<BedRequest {self.id} {self.status}>"


class BedAssignment(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A bed placed against a request."""

    __tablename__ = "bed_assignments"
    __table_args__ = (
        CheckConstraint(
            "released_at IS NULL OR released_at >= assigned_at",
            name="period_order",
        ),
        # A bed can hold at most one live assignment. Enforced by the database
        # rather than by application code, because a double-booked bed is exactly
        # the failure this whole project exists to prevent.
        Index(
            "uq_bed_assignments_one_active_per_bed",
            "location_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE' AND deleted_at IS NULL"),
        ),
    )

    bed_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("bed_requests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[BedAssignmentStatus] = mapped_column(
        enum_column(BedAssignmentStatus, "bed_assignment_status"),
        nullable=False,
        default=BedAssignmentStatus.ACTIVE,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bed_request: Mapped["BedRequest"] = relationship(back_populates="assignments")
    location: Mapped["Location"] = relationship()

    def __repr__(self) -> str:
        return f"<BedAssignment {self.id} {self.status}>"


class BedStatusEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only timeline of every bed status transition.

    No soft delete and no update path: this table is the evidence that a state
    change happened. Rewriting history here would defeat its only purpose, and it
    is also the raw material for the occupancy metrics planned for later phases.
    """

    __tablename__ = "bed_status_events"

    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("encounters.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    previous_status: Mapped[BedStatus | None] = mapped_column(
        enum_column(BedStatus, "bed_event_previous_status"), nullable=True
    )
    new_status: Mapped[BedStatus] = mapped_column(
        enum_column(BedStatus, "bed_event_new_status"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    location: Mapped["Location"] = relationship()

    def __repr__(self) -> str:
        return f"<BedStatusEvent {self.previous_status}->{self.new_status}>"
