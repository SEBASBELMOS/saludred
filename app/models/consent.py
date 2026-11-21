"""Patient consent by destination (R32, Ley 1581).

A consent binds one patient to exactly one destination for one validity window.
In-network rows point at an ``organizations`` row; partner rows point at an
``interop_clients`` row (the partner has no organization in this network). The
database enforces "exactly one of the two" with a CHECK.

Rows are immutable, like referrals: renewal is a NEW row and revocation records
``revoked_at``/``revoked_by`` on the existing one, so the full consent history
survives for the audit trail. There is no soft delete on purpose — a revoked or
expired consent is history, not garbage.

The public status (``ConsentStatus``) is DERIVED from the validity columns; it is
never stored, so the stored facts and the reported status cannot disagree.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.interop import InteropClient
from app.models.organization import Organization
from app.models.patient import Patient

# Plain IS NULL / IS NOT NULL logic on purpose: SQLite enforces CHECKs but has no
# row-counting or set operators, and the disposable verification DB is SQLite.
# Must stay byte-identical to the migration's EXACTLY_ONE_DESTINATION.
EXACTLY_ONE_DESTINATION = (
    "(organization_id IS NULL AND interop_client_id IS NOT NULL)"
    " OR (organization_id IS NOT NULL AND interop_client_id IS NULL)"
)


class Consent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(EXACTLY_ONE_DESTINATION, name="exactly_one_destination"),
        # Partial: each row has exactly one NULL destination, so each index only
        # covers its own half of the table.
        Index(
            "ix_consents_patient_id_organization_id",
            "patient_id",
            "organization_id",
            postgresql_where=text("organization_id IS NOT NULL"),
            sqlite_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "ix_consents_patient_id_interop_client_id",
            "patient_id",
            "interop_client_id",
            postgresql_where=text("interop_client_id IS NOT NULL"),
            sqlite_where=text("interop_client_id IS NOT NULL"),
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Exactly one of the two destination columns is set (see the CHECK above).
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # The partner row id; the service resolves the business client_id.
    interop_client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("interop_clients.id", ondelete="RESTRICT"),
        nullable=True,
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL = indefinite until revoked.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT", use_alter=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT", use_alter=True), nullable=True
    )

    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])
    organization: Mapped[Organization | None] = relationship(foreign_keys=[organization_id])
    interop_client: Mapped[InteropClient | None] = relationship(foreign_keys=[interop_client_id])

    def __repr__(self) -> str:
        destination = (
            f"org={self.organization_id}" if self.organization_id is not None
            else f"client={self.interop_client_id}"
        )
        return f"<Consent {self.id} patient={self.patient_id} {destination}>"
