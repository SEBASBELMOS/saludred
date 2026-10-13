"""Persistence and reading of the triage cohort."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.cohort import CohortCluster, CohortRecord
from app.models.enums import CohortOrigin, CohortZone
from app.services.cohort_builder import CohortResult

ZONE_LABEL = {
    CohortZone.FAR: "lejos",
    CohortZone.INTERMEDIATE: "intermedio",
    CohortZone.NEAR: "cerca",
}


def persist_cohort(db: Session, result: CohortResult) -> None:
    """Replace the stored cohort with ``result`` in one transaction.

    Records go first because they reference the clusters. If anything fails the
    whole replacement rolls back and the previous cohort stays intact, so
    running the builder twice always leaves exactly one cohort.
    """

    try:
        db.execute(delete(CohortRecord))
        db.execute(delete(CohortCluster))
        ids: dict[int, uuid.UUID] = {}
        for cluster in result.clusters:
            ids[cluster.index] = uuid.uuid4()
            db.add(
                CohortCluster(
                    id=ids[cluster.index],
                    cluster_index=cluster.index,
                    zone=CohortZone(cluster.zone),
                    description=cluster.description,
                    admission_rate=cluster.admission_rate,
                    centroid=cluster.centroid,
                    silhouette=cluster.silhouette,
                )
            )
        db.flush()
        db.add_all(
            CohortRecord(
                id=row.id,
                origin=CohortOrigin(row.origin),
                cluster_id=ids[row.cluster],
                source_ref=row.source_ref,
                temperature_c=row.temperature_c,
                heart_rate=row.heart_rate,
                resp_rate=row.resp_rate,
                o2sat=row.o2sat,
                sbp=row.sbp,
                dbp=row.dbp,
                pain=row.pain,
                acuity=row.acuity,
                admitted=row.admitted,
            )
            for row in result.rows
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def cluster_overview(db: Session) -> list[dict[str, object]]:
    """One entry per cluster with its real and synthetic counts; [] if not built."""

    counts: dict[tuple[uuid.UUID, CohortOrigin], int] = {
        (cluster_id, origin): total
        for cluster_id, origin, total in db.execute(
            select(CohortRecord.cluster_id, CohortRecord.origin, func.count()).group_by(
                CohortRecord.cluster_id, CohortRecord.origin
            )
        )
    }
    clusters = db.scalars(select(CohortCluster).order_by(CohortCluster.cluster_index))
    return [
        {
            "cluster": c.cluster_index,
            "zona": ZONE_LABEL[c.zone],
            "descripcion": c.description,
            "n_reales": counts.get((c.id, CohortOrigin.REAL), 0),
            "n_sinteticos": counts.get((c.id, CohortOrigin.SYNTHETIC), 0),
            "tasa_ingreso": c.admission_rate,
            "silueta": c.silhouette,
        }
        for c in clusters
    ]
