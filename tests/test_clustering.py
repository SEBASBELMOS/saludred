"""Automatic grouping of patients: method, determinism and honesty.

The dataset is the synthetic seed in SQLite in memory. The thresholds are
deliberately loose -- they check that the vital signs carry real structure and
that the method finds it, not that it lands on one exact number.
"""

from __future__ import annotations

import contextlib
import io
import random

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

import scripts.seed as seed_module
from app.models import Base, Patient
from app.services import clustering


@pytest.fixture(scope="module")
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            seed_module.seed(reset=True, patient_count=160)
    finally:
        seed_module.SessionLocal = original
    with factory() as session:
        yield session


@pytest.fixture(scope="module")
def result(db: Session) -> dict:
    return clustering.patient_clusters(db)


def test_every_patient_with_measurements_is_grouped(result: dict) -> None:
    assert result["patients"] == 160
    assert sum(c["patients"] for c in result["clusters"]) == 160
    assert all(c["patients"] > 0 for c in result["clusters"])


def test_the_number_of_groups_is_the_best_silhouette(result: dict) -> None:
    """The decision on k is taken from the data, not from knowing there are
    six profiles."""

    best = max(result["silhouette_by_k"], key=lambda c: c["silhouette"])
    assert result["k"] == best["k"]
    assert result["k_chosen_by"] == "silhouette"


def test_the_groups_recover_the_clinical_profiles(result: dict) -> None:
    """Well above chance: the vital signs really do carry the profiles."""

    assert result["adjusted_rand_index"] > 0.4
    assert result["purity"] > 0.6


def test_the_profile_is_never_used_to_build_the_groups(db: Session) -> None:
    """Erasing every label must not change a single group.

    If the algorithm could see the profile, agreeing with it afterwards would
    prove nothing about the data.
    """

    before = clustering.patient_clusters(db)
    saved = {p.id: p.clinical_profile for p in db.query(Patient)}
    db.execute(update(Patient).values(clinical_profile=None))
    try:
        after = clustering.patient_clusters(db)
    finally:
        for patient_id, profile in saved.items():
            db.execute(update(Patient).where(Patient.id == patient_id).values(clinical_profile=profile))

    assert after["k"] == before["k"]
    assert sorted(c["patients"] for c in after["clusters"]) == sorted(c["patients"] for c in before["clusters"])
    assert after["adjusted_rand_index"] is None


def test_the_result_is_reproducible(db: Session, result: dict) -> None:
    again = clustering.patient_clusters(db)
    assert again["silhouette_by_k"] == result["silhouette_by_k"]
    assert [c["centroid"] for c in again["clusters"]] == [c["centroid"] for c in result["clusters"]]


def test_a_requested_number_of_groups_is_honoured(db: Session) -> None:
    six = clustering.patient_clusters(db, k=6)
    assert six["k"] == 6
    assert six["k_chosen_by"] == "requested"
    assert len(six["clusters"]) == 6


def test_the_adjusted_rand_index_behaves_as_defined() -> None:
    truth = ["a"] * 30 + ["b"] * 30 + ["c"] * 30
    same = [0] * 30 + [1] * 30 + [2] * 30
    assert clustering.adjusted_rand_index(truth, same) == pytest.approx(1.0)

    # Renaming the groups changes nothing: only the partition matters.
    renamed = [2] * 30 + [0] * 30 + [1] * 30
    assert clustering.adjusted_rand_index(truth, renamed) == pytest.approx(1.0)

    # Random labels land near zero, which is the point of the adjustment.
    rng = random.Random(7)
    noise = [rng.randrange(3) for _ in truth]
    assert abs(clustering.adjusted_rand_index(truth, noise)) < 0.1


def test_silhouette_rewards_separated_groups() -> None:
    tight = [[0.0, 0.0], [0.1, 0.0], [10.0, 10.0], [10.1, 10.0]]
    distances = clustering.distance_matrix(tight)
    assert clustering.silhouette(distances, [0, 0, 1, 1]) > 0.9
    assert clustering.silhouette(distances, [0, 1, 0, 1]) < 0
