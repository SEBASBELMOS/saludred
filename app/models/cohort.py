"""Triage cohort: real emergency-department rows plus cluster-based synthetic rows.

The real rows come from MIMIC-IV-ED Demo. Synthetic rows are never free: each
one is interpolated between two real neighbours of the same cluster, is marked
``origin = SYNTHETIC`` and points to its cluster and to the rows it came from.
The clusters are read clinically (far from / intermediate / near the critical
case), which is why they live in their own table with a written description.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import CohortOrigin, CohortZone, enum_column
from app.models.governance import JSON_COLUMN


class CohortCluster(UUIDPrimaryKeyMixin, Base):
    """One group found by k-means, with its clinical reading."""

    __tablename__ = "cohort_clusters"

    # 1-based, ordered by admission rate: 1 is the farthest from the critical case.
    cluster_index: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    zone: Mapped[CohortZone] = mapped_column(
        enum_column(CohortZone, "cohort_zone"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(600), nullable=False)
    # Share of admitted stays among the REAL rows of the cluster.
    admission_rate: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False
    )
    # {feature: mean} in clinical units, over the REAL rows of the cluster.
    centroid: Mapped[dict[str, Any]] = mapped_column(JSON_COLUMN, nullable=False)
    silhouette: Mapped[float | None] = mapped_column(
        Numeric(6, 4, asdecimal=False), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CohortCluster {self.cluster_index} {self.zone}>"


class CohortRecord(UUIDPrimaryKeyMixin, Base):
    """One triage row: vital signs, ESI acuity and whether the stay ended in admission."""

    __tablename__ = "cohort_records"
    __table_args__ = (
        CheckConstraint("acuity BETWEEN 1 AND 5", name="acuity_range"),
    )

    origin: Mapped[CohortOrigin] = mapped_column(
        enum_column(CohortOrigin, "cohort_origin"), nullable=False
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("cohort_clusters.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # REAL: "mimic-iv-ed-demo:stay_id=<id>". SYNTHETIC: "base=<uuid>;vecino=<uuid>;u=<0.xx>".
    source_ref: Mapped[str] = mapped_column(String(200), nullable=False)

    temperature_c: Mapped[float] = mapped_column(
        Numeric(4, 1, asdecimal=False), nullable=False
    )
    heart_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    resp_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    o2sat: Mapped[int] = mapped_column(Integer, nullable=False)
    sbp: Mapped[int] = mapped_column(Integer, nullable=False)
    dbp: Mapped[int] = mapped_column(Integer, nullable=False)
    pain: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acuity: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CohortRecord {self.origin} acuity={self.acuity}>"
