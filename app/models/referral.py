"""Referrals between clinicians (R08).

A medico refers a patient to an especialista with a reason. The specialist
accepts (or rejects) and, once the consultation is done, writes a report.

A referral is never deleted, like an AI report: it is part of the care trail, so
the table has no soft-delete columns. A *rejected* referral is kept as evidence
and grants nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ReferralStatus, enum_column
from app.models.identity import User
from app.models.patient import Patient

# States in which the target specialist may read the patient's record.
ACCESS_GRANTING_STATES = (
    ReferralStatus.PENDING,
    ReferralStatus.ACCEPTED,
    ReferralStatus.COMPLETED,
)


class Referral(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "referrals"
    __table_args__ = (
        Index("ix_referrals_specialist_user_id_estado", "specialist_user_id", "estado"),
        # A referral made by the agent is keyed by <thread>:<tool_call_id>: retrying an
        # approval can never create a second one (the database enforces it).
        Index(
            "uq_referrals_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # NULL only for a referral that arrives from a partner system through the
    # interoperability API (``origen_externo`` then names the client).
    referring_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    specialist_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    motivo: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Python attribute ``status``; the column keeps the contract name ``estado``.
    status: Mapped[ReferralStatus] = mapped_column(
        "estado",
        enum_column(ReferralStatus, "referral_status"),
        nullable=False,
        default=ReferralStatus.PENDING,
    )
    motivo_rechazo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    informe_especialista: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Id of the ServiceRequest on the FHIR server, once synchronized.
    fhir_resource_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Set only by the agent (see the index above); NULL for every other referral.
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Partner system that sent it (interop client id) and its own reference for the
    # ServiceRequest. Both NULL for a referral made inside SaludRed.
    origen_externo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    referencia_externa: Mapped[str | None] = mapped_column(String(120), nullable=True)

    patient: Mapped[Patient] = relationship(foreign_keys=[patient_id])
    referring_user: Mapped[User | None] = relationship(foreign_keys=[referring_user_id])
    specialist_user: Mapped[User] = relationship(foreign_keys=[specialist_user_id])

    def __repr__(self) -> str:
        return f"<Referral {self.id} {self.status}>"
