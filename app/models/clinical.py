"""Clinical events: encounters and observations."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import (
    EncounterClass,
    EncounterStatus,
    ObservationStatus,
    Priority,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.beds import BedRequest
    from app.models.organization import Location, Organization
    from app.models.patient import Patient


class Encounter(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A clinical episode at one IPS.

    ``status`` and ``encounter_class`` store FHIR R4 codes verbatim because both
    are required elements (1..1) of the Encounter resource: a mapper that omits
    either produces a payload the FHIR server rejects.

    ``location_id`` points at the bed actually occupied and is what feeds
    ``Encounter.location``, tying the bed-management domain into the standard.
    """

    __tablename__ = "encounters"
    __table_args__ = (
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="period_order",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    encounter_class: Mapped[EncounterClass] = mapped_column(
        enum_column(EncounterClass, "encounter_class"), nullable=False
    )
    status: Mapped[EncounterStatus] = mapped_column(
        enum_column(EncounterStatus, "encounter_status"), nullable=False, index=True
    )
    priority: Mapped[Priority] = mapped_column(
        enum_column(Priority, "encounter_priority"), nullable=False, default=Priority.ROUTINE
    )
    reason_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="encounters")
    organization: Mapped["Organization"] = relationship(back_populates="encounters")
    location: Mapped["Location | None"] = relationship()
    observations: Mapped[list["Observation"]] = relationship(back_populates="encounter")
    bed_requests: Mapped[list["BedRequest"]] = relationship(back_populates="encounter")

    def __repr__(self) -> str:
        return f"<Encounter {self.id} {self.status}>"


class Observation(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A measurement taken during an encounter.

    Codes are LOINC and units are UCUM. Both systems are stored explicitly rather
    than assumed, so a future non-LOINC observation does not silently inherit the
    wrong code system.
    """

    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(
            "value_numeric IS NOT NULL OR value_text IS NOT NULL",
            name="has_value",
        ),
        CheckConstraint(
            "value_numeric IS NULL OR unit IS NOT NULL",
            name="numeric_needs_unit",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    encounter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("encounters.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    status: Mapped[ObservationStatus] = mapped_column(
        enum_column(ObservationStatus, "observation_status"),
        nullable=False,
        default=ObservationStatus.FINAL,
    )
    code_system: Mapped[str] = mapped_column(
        String(200), nullable=False, default="http://loinc.org"
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    display: Mapped[str] = mapped_column(String(200), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit_system: Mapped[str] = mapped_column(
        String(200), nullable=False, default="http://unitsofmeasure.org"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    patient: Mapped["Patient"] = relationship(back_populates="observations")
    encounter: Mapped["Encounter"] = relationship(back_populates="observations")

    def __repr__(self) -> str:
        return f"<Observation {self.code} {self.value_numeric or self.value_text}>"
