"""Patient demographics."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AuthorshipMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import AdministrativeGender, DocumentType, enum_column

if TYPE_CHECKING:
    from app.models.clinical import Encounter, Observation
    from app.models.identity import User
    from app.models.organization import Organization


class Patient(
    UUIDPrimaryKeyMixin, TimestampMixin, AuthorshipMixin, SoftDeleteMixin, Base
):
    """A person affiliated to the EPS.

    The ``(document_type, document_number)`` pair is the business identifier that
    is carried over to ``Patient.identifier`` in FHIR. Reusing the real-world key
    instead of the internal UUID is what makes the synchronization idempotent:
    the FHIR server can resolve "this same person" on its own.
    """

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("document_type", "document_number", name="uq_patients_document_type_document_number"),
    )

    document_type: Mapped[DocumentType] = mapped_column(
        enum_column(DocumentType, "document_type"), nullable=False
    )
    document_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[AdministrativeGender] = mapped_column(
        enum_column(AdministrativeGender, "administrative_gender"), nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)

    eps_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    eps_organization: Mapped["Organization"] = relationship(
        back_populates="affiliated_patients", foreign_keys=[eps_organization_id]
    )
    encounters: Mapped[list["Encounter"]] = relationship(
        back_populates="patient", foreign_keys="Encounter.patient_id"
    )
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="patient", foreign_keys="Observation.patient_id"
    )
    user_account: Mapped["User | None"] = relationship(
        back_populates="patient", foreign_keys="User.patient_id", uselist=False
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def business_identifier(self) -> str:
        """Value used as Patient.identifier when synchronizing to FHIR."""
        return f"{self.document_type.value}-{self.document_number}"

    def __repr__(self) -> str:
        return f"<Patient {self.business_identifier}>"
