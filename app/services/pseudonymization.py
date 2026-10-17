"""Data minimization and pseudonymization towards the AI service (R12).

Two rules, both enforced here and nowhere else:

1. **Allowlist.** The payload is built field by field from the model's own
   feature list. Anything not named here -- name, document, e-mail, phone,
   address, birth date, the real patient id -- cannot reach the model because no
   code path ever reads it. A blocklist would leak the next column someone adds.
2. **Pseudonym.** The model sees ``pz-`` plus 16 hex digits of
   HMAC-SHA256(key, patient id). It is stable (the same patient always maps to
   the same pseudonym, so reports can be correlated) and one-way (without the
   key it cannot be reversed or recomputed from a known id).

The exact payload sent is persisted by the caller, so what the model received is
always inspectable (Ley 1581 de 2012: finalidad y circulacion restringida).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.clinical import Encounter, Observation
from app.models.enums import AdministrativeGender, ObservationStatus
from app.models.loinc import (
    BODY_TEMPERATURE,
    DIASTOLIC_BP,
    HEART_RATE,
    OXYGEN_SATURATION,
    RESPIRATORY_RATE,
    SYSTOLIC_BP,
)
from app.models.patient import Patient

PSEUDONYM_PREFIX = "pz-"
PSEUDONYM_HEX_DIGITS = 16

# LOINC code -> field of the criticality model, with the unit it must come in.
# A value in another unit is skipped, never converted by guesswork.
LOINC_TO_FIELD: dict[str, tuple[str, str]] = {
    BODY_TEMPERATURE.code: ("temperature_c", "Cel"),
    HEART_RATE.code: ("heart_rate", "/min"),
    RESPIRATORY_RATE.code: ("resp_rate", "/min"),
    OXYGEN_SATURATION.code: ("o2sat", "%"),
    SYSTOLIC_BP.code: ("sbp", "mm[Hg]"),
    DIASTOLIC_BP.code: ("dbp", "mm[Hg]"),
}

# Every key the payload may ever contain. Tests assert nothing else appears.
PAYLOAD_FIELDS = (
    "pseudonym",
    "age",
    "sex",
    *(field for field, _unit in LOINC_TO_FIELD.values()),
    "acuity",
)
CLINICAL_FIELDS = tuple(f for f in PAYLOAD_FIELDS if f != "pseudonym")
# The six vital signs the automatic analysis waits for before running on its own.
CORE_VITAL_FIELDS = tuple(field for field, _unit in LOINC_TO_FIELD.values())


def pseudonym_for(patient_id: uuid.UUID | str) -> str:
    digest = hmac.new(
        get_settings().pseudonym_key.encode("utf-8"),
        str(patient_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return PSEUDONYM_PREFIX + digest[:PSEUDONYM_HEX_DIGITS]


def age_in_years(birth_date: date, today: date | None = None) -> int:
    today = today or date.today()
    years = today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )
    return max(years, 0)


def latest_vitals(
    db: Session,
    patient_id: uuid.UUID,
    *,
    organization_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
) -> dict[str, float]:
    """Most recent usable value of each model variable for the patient.

    ``organization_id`` restricts the observations to one IPS, so an
    institution-bound clinician only sends what it could already read.
    ``encounter_id`` restricts them to one encounter (the automatic analysis
    works per encounter).
    """

    stmt = (
        select(Observation)
        .where(
            Observation.patient_id == patient_id,
            Observation.deleted_at.is_(None),
            Observation.status != ObservationStatus.CANCELLED,
            Observation.code.in_(LOINC_TO_FIELD),
            Observation.value_numeric.is_not(None),
        )
        .order_by(Observation.observed_at.desc())
    )
    if encounter_id is not None:
        stmt = stmt.where(Observation.encounter_id == encounter_id)
    if organization_id is not None:
        stmt = stmt.join(Encounter, Observation.encounter_id == Encounter.id).where(
            Encounter.organization_id == organization_id
        )

    values: dict[str, float] = {}
    for observation in db.scalars(stmt):
        field, unit = LOINC_TO_FIELD[observation.code]
        if field in values or observation.unit != unit:
            continue
        values[field] = float(observation.value_numeric)
    return values


def build_criticality_payload(
    db: Session,
    patient: Patient,
    *,
    acuity: int | None = None,
    organization_id: uuid.UUID | None = None,
    encounter_id: uuid.UUID | None = None,
) -> dict[str, object]:
    """The only thing the AI service ever receives about a patient."""

    sex = {
        AdministrativeGender.MALE: "male",
        AdministrativeGender.FEMALE: "female",
    }.get(patient.gender)
    payload: dict[str, object] = {
        "pseudonym": pseudonym_for(patient.id),
        "age": age_in_years(patient.birth_date),
        "sex": sex,
        **latest_vitals(
            db, patient.id, organization_id=organization_id, encounter_id=encounter_id
        ),
    }
    if acuity is not None:
        payload["acuity"] = acuity
    # Missing values are omitted, not sent as null: the model imputes and reports.
    return {key: value for key, value in payload.items() if value is not None}
