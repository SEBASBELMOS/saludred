"""Network topology: EPS, IPS and the physical locations they operate."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import BedStatus, LocationType, OrganizationType, enum_column

if TYPE_CHECKING:
    from app.models.clinical import Encounter
    from app.models.identity import User
    from app.models.patient import Patient


class Organization(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """An EPS or one of the IPS it coordinates.

    Both live in a single self-referencing table instead of two parallel tables.
    Adding an IPS to the network is then an INSERT, never a migration, which is
    what "scaling horizontally" has to mean at the schema level.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "(organization_type = 'EPS' AND parent_organization_id IS NULL)"
            " OR (organization_type = 'IPS' AND parent_organization_id IS NOT NULL)",
            name="hierarchy",
        ),
        CheckConstraint(
            "parent_organization_id IS NULL OR parent_organization_id <> id",
            name="not_self_parent",
        ),
    )

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    organization_type: Mapped[OrganizationType] = mapped_column(
        enum_column(OrganizationType, "organization_type"), nullable=False, index=True
    )
    parent_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    parent: Mapped["Organization | None"] = relationship(
        remote_side="Organization.id", back_populates="children"
    )
    children: Mapped[list["Organization"]] = relationship(back_populates="parent")
    locations: Mapped[list["Location"]] = relationship(back_populates="organization")
    users: Mapped[list["User"]] = relationship(back_populates="organization")
    encounters: Mapped[list["Encounter"]] = relationship(back_populates="organization")
    affiliated_patients: Mapped[list["Patient"]] = relationship(
        back_populates="eps_organization"
    )

    def __repr__(self) -> str:
        return f"<Organization {self.organization_type} {self.code}>"


class Location(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A facility, ward, room or bed belonging to an IPS.

    The same self-referencing pattern as Organization, for the same reason: a new
    ward or a new bed must never require a schema change.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_locations_organization_id_code"),
        CheckConstraint(
            "(location_type = 'BED' AND status IS NOT NULL)"
            " OR (location_type <> 'BED' AND status IS NULL)",
            name="status_only_for_beds",
        ),
        CheckConstraint(
            "parent_location_id IS NULL OR parent_location_id <> id",
            name="not_self_parent",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    location_type: Mapped[LocationType] = mapped_column(
        enum_column(LocationType, "location_type"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[BedStatus | None] = mapped_column(
        enum_column(BedStatus, "bed_status"), nullable=True, index=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="locations")
    parent: Mapped["Location | None"] = relationship(
        remote_side="Location.id", back_populates="children"
    )
    children: Mapped[list["Location"]] = relationship(back_populates="parent")

    def __repr__(self) -> str:
        return f"<Location {self.location_type} {self.code}>"
