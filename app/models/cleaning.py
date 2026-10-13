"""Evidence that a bed was cleaned: a checklist, a written report and a photo.

Terminal cleaning after a discharge or transfer is a real step of the bed cycle
(CDC 2019, Environmental Cleaning; Colombian Res. 3100/2019 lists it among the
priority processes). This table is the proof that the step happened.

Records are append-and-review only: no soft delete and no edit path. Evidence
that can be rewritten stops being evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Uuid,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    CleaningDisinfectant,
    CleaningIsolation,
    CleaningReviewStatus,
    enum_column,
)
from app.models.governance import JSON_COLUMN
from app.models.organization import Location

# High-touch surfaces and areas of a terminal cleaning, in inspection order.
# Source: CDC "Environmental Checklist for Monitoring Terminal Cleaning". The
# keys are the stored contract; the labels are only for the API docs and the UI.
CHECKLIST_ITEMS: dict[str, str] = {
    "barandas": "Barandas de la cama",
    "colchon": "Colchon",
    "mesa_auxiliar": "Mesa auxiliar",
    "timbre_y_controles": "Timbre y controles de la cama",
    "poste_de_suero": "Poste de suero",
    "manijas_e_interruptores": "Manijas de puertas e interruptores",
    "cortinas": "Cortinas",
    "bano": "Bano",
    "piso": "Piso",
    "residuos": "Retiro de residuos",
    "ropa_de_cama": "Cambio de ropa de cama",
}


class BedCleaningRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One terminal cleaning of one bed, with its evidence and its review."""

    __tablename__ = "bed_cleaning_records"
    __table_args__ = (
        CheckConstraint("finished_at >= started_at", name="period_order"),
    )

    location_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("locations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    isolation: Mapped[CleaningIsolation] = mapped_column(
        enum_column(CleaningIsolation, "cleaning_isolation"), nullable=False
    )
    disinfectant: Mapped[CleaningDisinfectant] = mapped_column(
        enum_column(CleaningDisinfectant, "cleaning_disinfectant"), nullable=False
    )
    curtains_changed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    # {item_key: bool} over CHECKLIST_ITEMS.
    checklist: Mapped[dict[str, Any]] = mapped_column(JSON_COLUMN, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The photo lives in the row (bytea in PostgreSQL): a handful of small
    # images per bed cycle does not justify an object store yet.
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    photo_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[CleaningReviewStatus] = mapped_column(
        enum_column(CleaningReviewStatus, "cleaning_review_status"),
        nullable=False,
        default=CleaningReviewStatus.PENDING_REVIEW,
        index=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Copied from the CLEANING entry event of the bed: the AI service's guess
    # of whether this cleaning would end up late. NULL when there was none.
    predicted_delay_probability: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    predicted_delay: Mapped[bool | None] = mapped_column(nullable=True)
    delay_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)

    location: Mapped[Location] = relationship(foreign_keys=[location_id])

    @property
    def has_photo(self) -> bool:
        return self.photo_size is not None

    def __repr__(self) -> str:
        return f"<BedCleaningRecord {self.id} {self.status}>"
