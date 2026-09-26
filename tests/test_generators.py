"""Properties the synthetic data must hold, checked over many draws.

These are not example-based tests. A generator can produce one valid record by
luck; what matters is that it cannot produce an invalid one, so every check
here runs over thousands of draws and asserts a rule that must never break.
"""

from __future__ import annotations

import random
import statistics

import pytest

from app.models.enums import AdministrativeGender, ClinicalProfile, Priority
from app.models.loinc import (
    BODY_HEIGHT,
    BODY_WEIGHT,
    DIASTOLIC_BP,
    GLUCOSE,
    PLAUSIBLE_RANGE,
    SYSTOLIC_BP,
)
from scripts.generators import (
    PROFILE_WEIGHTS,
    cleaning_minutes,
    imaging_studies,
    pick_age,
    pick_priority,
    pick_profile,
    stay_hours,
    vital_signs,
    wait_minutes,
)
from app.models.catalog import MODALITY_VOLUME, PROFILE_TARGETS

DRAWS = 3000


@pytest.fixture
def rng() -> random.Random:
    return random.Random(424242)


def _sample(rng: random.Random, draws: int = DRAWS):
    for _ in range(draws):
        profile = pick_profile(rng)
        gender = rng.choice(list(AdministrativeGender))
        yield profile, {v.code: v.value for v in vital_signs(rng, profile, gender)}


def test_systolic_is_always_above_diastolic(rng: random.Random) -> None:
    """The one rule a blood pressure reading can never break.

    It holds by construction -- the diastolic value is a fraction of the
    systolic one -- and this test exists so that stops being true loudly if
    anyone ever draws them independently.
    """

    for _profile, vitals in _sample(rng):
        assert vitals[SYSTOLIC_BP.code] > vitals[DIASTOLIC_BP.code]


def test_every_value_stays_physiologically_plausible(rng: random.Random) -> None:
    for _profile, vitals in _sample(rng):
        for code, value in vitals.items():
            low, high = PLAUSIBLE_RANGE[code]
            assert low <= value <= high, f"{code}={value}"


def test_weight_and_height_agree_on_a_possible_body(rng: random.Random) -> None:
    """Weight is derived from height and a BMI target, never drawn apart.

    Independent draws produce 45 kg at 1.95 m, which no reviewer would
    believe and which would wreck any statistic computed on the cohort.
    """

    for _profile, vitals in _sample(rng):
        height_m = vitals[BODY_HEIGHT.code] / 100
        bmi = vitals[BODY_WEIGHT.code] / height_m**2
        assert 12 < bmi < 60, f"IMC {bmi:.1f}"


def test_profiles_separate_on_the_variable_that_defines_them(
    rng: random.Random,
) -> None:
    """Diabetic patients must run higher glucose than healthy ones.

    Without this the profile label would be noise and any grouping of the
    data would be measuring nothing.
    """

    glucose: dict[ClinicalProfile, list[float]] = {p: [] for p in ClinicalProfile}
    for profile, vitals in _sample(rng, 6000):
        glucose[profile].append(vitals[GLUCOSE.code])

    healthy = statistics.mean(glucose[ClinicalProfile.HEALTHY])
    diabetic = statistics.mean(glucose[ClinicalProfile.DIABETIC])
    assert diabetic > healthy + 50


def test_profiles_overlap_so_grouping_is_not_trivial(rng: random.Random) -> None:
    """The groups must blur at the edges.

    Perfectly separated clusters would make any algorithm look brilliant and
    prove nothing about the data or about the method.
    """

    healthy: list[float] = []
    diabetic: list[float] = []
    for profile, vitals in _sample(rng, 6000):
        if profile == ClinicalProfile.HEALTHY:
            healthy.append(vitals[GLUCOSE.code])
        elif profile == ClinicalProfile.DIABETIC:
            diabetic.append(vitals[GLUCOSE.code])

    assert max(healthy) > min(diabetic), "los grupos no se solapan en absoluto"


def test_age_respects_the_profile_range(rng: random.Random) -> None:
    for _ in range(DRAWS):
        profile = pick_profile(rng)
        low, high = PROFILE_TARGETS[profile].age_range
        assert low <= pick_age(rng, profile) <= high


def test_every_profile_can_be_drawn(rng: random.Random) -> None:
    drawn = {pick_profile(rng) for _ in range(DRAWS)}
    assert drawn == set(PROFILE_WEIGHTS) == set(ClinicalProfile)


def test_emergencies_wait_less_than_routine_admissions(rng: random.Random) -> None:
    """If the queue did not reward priority, triage would be decoration."""

    waits = {
        priority: sorted(wait_minutes(rng, priority) for _ in range(DRAWS))
        for priority in Priority
    }
    median = {p: v[len(v) // 2] for p, v in waits.items()}
    assert median[Priority.EMERGENCY] < median[Priority.URGENT]
    assert median[Priority.URGENT] < median[Priority.ROUTINE]
    assert all(value > 0 for values in waits.values() for value in values)


def test_waiting_times_have_a_long_right_tail(rng: random.Random) -> None:
    """Real queues are skewed: most cases are quick, a few are very slow.

    A symmetric curve would hide exactly the cases the network needs to see.
    """

    values = sorted(wait_minutes(rng, Priority.ROUTINE) for _ in range(DRAWS))
    median = values[len(values) // 2]
    assert statistics.mean(values) > median


def test_sicker_profiles_stay_longer(rng: random.Random) -> None:
    def median_stay(profile: ClinicalProfile) -> float:
        return statistics.median(
            stay_hours(rng, profile, Priority.ROUTINE) for _ in range(DRAWS)
        )

    assert median_stay(ClinicalProfile.CARDIAC) > median_stay(ClinicalProfile.HEALTHY)


def test_cleaning_always_takes_some_time(rng: random.Random) -> None:
    """A bed is never available the instant the patient leaves."""

    assert all(cleaning_minutes(rng) >= 15 for _ in range(DRAWS))


def test_imaging_matches_the_profile_and_the_modality_volume(
    rng: random.Random,
) -> None:
    import datetime as dt

    start = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)
    seen_uids: set[str] = set()
    sequence = 1
    produced = 0

    for _ in range(DRAWS):
        profile = pick_profile(rng)
        for study in imaging_studies(rng, profile, start, sequence):
            produced += 1
            sequence += 1
            assert study.study_instance_uid not in seen_uids, "UID DICOM repetido"
            seen_uids.add(study.study_instance_uid)

            series_range, instance_range = MODALITY_VOLUME[study.modality]
            assert series_range[0] <= study.series_count <= series_range[1]
            low = study.series_count * instance_range[0]
            high = study.series_count * instance_range[1]
            assert low <= study.instance_count <= high
            assert study.started_at > start

    assert produced > 0
    # Imaging is ordered for some encounters, not all: an imaging table with a
    # row per encounter would answer no question worth asking.
    assert produced < DRAWS


def test_a_plain_radiograph_is_lighter_than_a_tomography(rng: random.Random) -> None:
    dx = MODALITY_VOLUME["DX"]
    ct = MODALITY_VOLUME["CT"]
    assert dx[1][1] < ct[1][0]


def test_priority_follows_the_clinical_picture(rng: random.Random) -> None:
    def emergency_share(profile: ClinicalProfile) -> float:
        hits = sum(
            pick_priority(rng, profile) == Priority.EMERGENCY for _ in range(DRAWS)
        )
        return hits / DRAWS

    assert emergency_share(ClinicalProfile.CARDIAC) > emergency_share(
        ClinicalProfile.HEALTHY
    )


def test_the_generator_is_reproducible() -> None:
    """Two runs with the same seed must produce identical data.

    A demo that changes under your feet between the rehearsal and the
    presentation is worse than no demo at all.
    """

    def draw() -> list[float]:
        local = random.Random(20260906)
        out: list[float] = []
        for _ in range(200):
            profile = pick_profile(local)
            out.extend(
                v.value
                for v in vital_signs(local, profile, AdministrativeGender.FEMALE)
            )
        return out

    assert draw() == draw()
