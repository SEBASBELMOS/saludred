"""Late-cleaning prediction for a bed entering CLEANING.

Operational only: the features describe the bed, the service and the moment --
never the patient who just left. The prediction is advisory and best effort. A
bed must move to CLEANING whether or not the AI service answers, so every
failure here is logged and turned into "no prediction".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.beds import BedRequest
from app.models.enums import BedRequestStatus, BedStatus, LocationType
from app.models.organization import Location
from app.services import ml_client
from app.services.errors import ServiceError

logger = logging.getLogger(__name__)

# Colombia has no daylight saving time, so a fixed offset is exact.
COLOMBIA = timezone(timedelta(hours=-5))
OPEN_REQUESTS = (BedRequestStatus.PENDING, BedRequestStatus.IN_REVIEW)


@dataclass(frozen=True)
class DelayPrediction:
    probability: Decimal
    late: bool
    model_version: str


def build_features(
    db: Session,
    bed: Location,
    *,
    release_reason: str | None = None,
    previous_stay_hours: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Operational features known at the moment the bed enters CLEANING.

    What the system does not know (staff on shift, isolation precautions,
    curtains) is left out; the AI service imputes it and says so.
    """

    local = (now or datetime.now(timezone.utc)).astimezone(COLOMBIA)
    same_service = [
        Location.organization_id == bed.organization_id,
        Location.location_type == LocationType.BED,
        Location.deleted_at.is_(None),
        Location.status != BedStatus.MAINTENANCE,
    ]
    if bed.service is not None:
        same_service.append(Location.service == bed.service)
    total = db.scalar(select(func.count()).select_from(Location).where(*same_service)) or 0
    occupied = (
        db.scalar(
            select(func.count())
            .select_from(Location)
            .where(*same_service, Location.status == BedStatus.OCCUPIED)
        )
        or 0
    )
    pending = (
        db.scalar(
            select(func.count())
            .select_from(Location)
            .where(
                Location.organization_id == bed.organization_id,
                Location.location_type == LocationType.BED,
                Location.deleted_at.is_(None),
                Location.status == BedStatus.CLEANING,
                Location.id != bed.id,
            )
        )
        or 0
    )
    waiting = db.scalar(
        select(BedRequest.id)
        .where(
            BedRequest.status.in_(OPEN_REQUESTS),
            BedRequest.deleted_at.is_(None),
            BedRequest.required_service == bed.service,
            (BedRequest.target_organization_id == bed.organization_id)
            | (BedRequest.requesting_organization_id == bed.organization_id),
        )
        .limit(1)
    )

    features: dict[str, Any] = {
        "service": bed.service,
        "release_reason": release_reason,
        "release_hour": local.hour,
        "weekday": local.isoweekday(),
        "previous_stay_hours": previous_stay_hours,
        "pending_cleaning_beds": pending,
        "service_occupancy": round(occupied / total, 3) if total else None,
        "patient_waiting": waiting is not None,
    }
    return {key: value for key, value in features.items() if value is not None}


def predict(db: Session, bed: Location, **kwargs: Any) -> DelayPrediction | None:
    """Ask the AI service; ``None`` on any failure (never raises)."""

    try:
        answer = ml_client.get_client().predict_cleaning_delay(build_features(db, bed, **kwargs))
        return DelayPrediction(
            probability=Decimal(str(round(float(answer["probability"]), 4))),
            late=bool(answer["positive"]),
            model_version=str(answer["model_version"]),
        )
    except (ServiceError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Cleaning-delay prediction skipped for bed %s: %s", bed.code, exc.__class__.__name__
        )
        return None
