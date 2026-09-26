"""Pure value generators for the synthetic dataset.

Nothing here touches the database. Every function takes a ``random.Random``
and returns plain values, which means the rules that make the data plausible
can be tested without a Postgres instance running.

The guiding rule is that the data must survive a second look. Random numbers
inside a valid range are easy; what is hard -- and what makes the dataset
useful for statistics -- is the *relationships* between them: the diastolic
pressure below the systolic, the weight consistent with the height, the
discharge after the admission, the CT with hundreds of images and the plain
radiograph with two.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.catalog import (
    MODALITY_VOLUME,
    PROFILE_IMAGING,
    PROFILE_TARGETS,
    ImagingOrder,
)
from app.models.enums import AdministrativeGender, ClinicalProfile, Priority
from app.models.loinc import (
    BODY_HEIGHT,
    BODY_TEMPERATURE,
    BODY_WEIGHT,
    DIASTOLIC_BP,
    GLUCOSE,
    HEART_RATE,
    OXYGEN_SATURATION,
    PLAUSIBLE_RANGE,
    RESPIRATORY_RATE,
    SYSTOLIC_BP,
)

# How often each profile shows up. Deliberately unbalanced: a network where
# every condition is equally frequent would make the cohort statistics
# meaningless, because the interesting question is precisely which groups
# concentrate the demand for beds.
PROFILE_WEIGHTS: dict[ClinicalProfile, float] = {
    ClinicalProfile.HEALTHY: 0.30,
    ClinicalProfile.HYPERTENSIVE: 0.22,
    ClinicalProfile.DIABETIC: 0.18,
    ClinicalProfile.CARDIAC: 0.12,
    ClinicalProfile.RESPIRATORY: 0.11,
    ClinicalProfile.ELDERLY_FRAIL: 0.07,
}

# DICOM UID root. This one belongs to the pydicom project and is used here
# only because the data is synthetic: a real institution must issue studies
# under the root assigned to it, otherwise two hospitals can mint the same
# identifier and a PACS will silently merge two different patients' studies.
DICOM_ROOT = "1.2.826.0.1.3680043.8.498"


def _clamp(code: str, value: float) -> float:
    low, high = PLAUSIBLE_RANGE[code]
    return min(max(value, low), high)


def pick_profile(rng: random.Random) -> ClinicalProfile:
    profiles = list(PROFILE_WEIGHTS)
    weights = [PROFILE_WEIGHTS[p] for p in profiles]
    return rng.choices(profiles, weights=weights, k=1)[0]


def pick_age(rng: random.Random, profile: ClinicalProfile) -> int:
    low, high = PROFILE_TARGETS[profile].age_range
    return rng.randint(low, high)


@dataclass(frozen=True)
class VitalReading:
    code: str
    display: str
    unit: str
    value: float


def vital_signs(
    rng: random.Random,
    profile: ClinicalProfile,
    gender: AdministrativeGender,
) -> list[VitalReading]:
    """Produce one coherent set of vital signs for a patient.

    Values are drawn around the profile's centre with gaussian noise, so the
    groups overlap at the edges instead of sitting in separate boxes. Every
    value is then clamped to the physiologically plausible range: the noise
    must not be able to invent a heart rate of 400.
    """

    t = PROFILE_TARGETS[profile]

    systolic = _clamp(SYSTOLIC_BP.code, rng.gauss(t.systolic, 12))
    # Derived from the systolic value, never drawn independently. This is what
    # guarantees systolic > diastolic in every row without a repair pass.
    diastolic = _clamp(
        DIASTOLIC_BP.code, systolic * rng.gauss(t.diastolic_ratio, 0.035)
    )

    # Height depends on sex; weight is then derived from the BMI target, so
    # the three numbers stay mutually consistent.
    height_mean = 176.0 if gender == AdministrativeGender.MALE else 162.0
    height_cm = _clamp(BODY_HEIGHT.code, rng.gauss(height_mean, 7.0))
    bmi = max(14.0, rng.gauss(t.bmi, 3.2))
    weight_kg = _clamp(BODY_WEIGHT.code, bmi * (height_cm / 100) ** 2)

    return [
        VitalReading(SYSTOLIC_BP.code, SYSTOLIC_BP.display, SYSTOLIC_BP.unit, round(systolic)),
        VitalReading(DIASTOLIC_BP.code, DIASTOLIC_BP.display, DIASTOLIC_BP.unit, round(diastolic)),
        VitalReading(
            HEART_RATE.code, HEART_RATE.display, HEART_RATE.unit,
            round(_clamp(HEART_RATE.code, rng.gauss(t.heart_rate, 9))),
        ),
        VitalReading(
            RESPIRATORY_RATE.code, RESPIRATORY_RATE.display, RESPIRATORY_RATE.unit,
            round(_clamp(RESPIRATORY_RATE.code, rng.gauss(t.respiratory_rate, 2.5))),
        ),
        VitalReading(
            BODY_TEMPERATURE.code, BODY_TEMPERATURE.display, BODY_TEMPERATURE.unit,
            round(_clamp(BODY_TEMPERATURE.code, rng.gauss(t.temperature, 0.45)), 1),
        ),
        VitalReading(
            OXYGEN_SATURATION.code, OXYGEN_SATURATION.display, OXYGEN_SATURATION.unit,
            round(_clamp(OXYGEN_SATURATION.code, rng.gauss(t.spo2, 2.5))),
        ),
        VitalReading(
            GLUCOSE.code, GLUCOSE.display, GLUCOSE.unit,
            round(_clamp(GLUCOSE.code, rng.gauss(t.glucose, t.glucose * 0.18))),
        ),
        VitalReading(BODY_WEIGHT.code, BODY_WEIGHT.display, BODY_WEIGHT.unit, round(weight_kg, 1)),
        VitalReading(BODY_HEIGHT.code, BODY_HEIGHT.display, BODY_HEIGHT.unit, round(height_cm)),
    ]


def pick_priority(rng: random.Random, profile: ClinicalProfile) -> Priority:
    """Choose the triage priority from the profile's emergency rate.

    Priority is not independent of the clinical picture: a cardiac patient
    arrives as an emergency far more often than a healthy one attending a
    routine check. Tying them together is what later makes the wait-time
    statistics by priority say something real.
    """

    roll = rng.random()
    emergency = PROFILE_TARGETS[profile].emergency_rate
    if roll < emergency:
        return Priority.EMERGENCY
    if roll < emergency + 0.35:
        return Priority.URGENT
    return Priority.ROUTINE


# Minutes from request to assignment, by priority: (centre, spread). An
# emergency that waits as long as a routine admission would mean the triage
# does nothing, and the whole priority queue would be decoration.
WAIT_MINUTES: dict[Priority, tuple[float, float]] = {
    Priority.EMERGENCY: (25, 12),
    Priority.URGENT: (110, 55),
    Priority.ROUTINE: (420, 240),
}


def wait_minutes(rng: random.Random, priority: Priority) -> int:
    centre, spread = WAIT_MINUTES[priority]
    # Lognormal, not gaussian: waiting times have a floor at zero and a long
    # right tail -- a few cases wait much longer than average, and no case
    # waits a negative amount of time. A symmetric curve gets both wrong.
    sigma = 0.55
    value = rng.lognormvariate(0, sigma) * centre
    return max(3, min(int(value), int(centre + spread * 12)))


def stay_hours(rng: random.Random, profile: ClinicalProfile, priority: Priority) -> int:
    """Length of stay in hours, longer for sicker profiles."""

    base = {
        ClinicalProfile.HEALTHY: 28,
        ClinicalProfile.HYPERTENSIVE: 54,
        ClinicalProfile.DIABETIC: 66,
        ClinicalProfile.CARDIAC: 110,
        ClinicalProfile.RESPIRATORY: 96,
        ClinicalProfile.ELDERLY_FRAIL: 130,
    }[profile]
    if priority == Priority.EMERGENCY:
        base *= 1.3
    return max(4, int(rng.lognormvariate(0, 0.45) * base))


def cleaning_minutes(rng: random.Random) -> int:
    """How long a bed stays in CLEANING before it is available again.

    This is the turnaround time the network cares about: a bed that exists
    but is not yet clean does not help the patient waiting in the hallway.
    """

    return max(15, int(rng.lognormvariate(0, 0.4) * 55))


@dataclass(frozen=True)
class StudyMetadata:
    modality: str
    body_site: str
    description: str
    study_instance_uid: str
    accession_number: str
    series_count: int
    instance_count: int
    started_at: datetime


def imaging_studies(
    rng: random.Random,
    profile: ClinicalProfile,
    encounter_start: datetime,
    sequence_start: int,
) -> list[StudyMetadata]:
    """Order zero or more imaging studies coherent with the profile.

    Not every encounter produces imaging -- if it did, the imaging table
    would be a copy of the encounter table with extra columns and would
    answer no question worth asking.
    """

    catalogue: tuple[ImagingOrder, ...] = PROFILE_IMAGING[profile]
    how_many = rng.choices([0, 1, 2], weights=[0.45, 0.40, 0.15], k=1)[0]
    how_many = min(how_many, len(catalogue))
    if how_many == 0:
        return []

    chosen = rng.sample(catalogue, k=how_many)
    studies: list[StudyMetadata] = []
    for offset, order in enumerate(chosen):
        series_range, instance_range = MODALITY_VOLUME[order.modality]
        series = rng.randint(*series_range)
        studies.append(
            StudyMetadata(
                modality=order.modality,
                body_site=order.body_site,
                description=order.description,
                study_instance_uid=f"{DICOM_ROOT}.{sequence_start + offset}",
                accession_number=f"ACC-{encounter_start.year}-{sequence_start + offset:07d}",
                series_count=series,
                # Instances are per series: a 3-series CT holds three times
                # the images of a single-series one.
                instance_count=series * rng.randint(*instance_range),
                started_at=encounter_start + timedelta(hours=rng.randint(1, 36)),
            )
        )
    return studies
