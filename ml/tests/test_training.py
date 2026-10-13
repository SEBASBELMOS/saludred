"""The evaluation protocol is part of the contract: real rows only, no leakage."""

from __future__ import annotations

from typing import Any

import numpy as np

import compare
import train
from augment import augment
from features import CRITICALITY_SPEC, FeatureSpec


def _spec_and_data():
    records, y, groups = train.load_criticality(train.DATA_DIR / "criticality_triage.csv")
    spec = FeatureSpec(CRITICALITY_SPEC.numeric, dict(CRITICALITY_SPEC.categorical))
    spec.fit_defaults(records)
    return spec, spec.matrix(records), y, groups


def _without_timings(value: Any) -> Any:
    """Fit times are wall-clock; everything else must be reproducible."""

    if isinstance(value, dict):
        return {
            k: _without_timings(v)
            for k, v in value.items()
            if k not in {"fit_seconds", "training_seconds"}
        }
    if isinstance(value, list):
        return [_without_timings(v) for v in value]
    return value


def test_training_data_has_no_identifiers() -> None:
    header = (train.DATA_DIR / "criticality_triage.csv").read_text().splitlines()[0]
    assert header == "group,age,sex,temperature_c,heart_rate,resp_rate,o2sat,sbp,dbp,acuity,admitted"
    assert len(train.read_csv(train.DATA_DIR / "criticality_triage.csv")) == 192


def test_metrics_are_computed_on_real_rows_only(artifacts) -> None:
    _, metrics = artifacts
    crit = metrics["criticality"]
    assert crit["data"]["real_rows"] == 192
    assert crit["validation"]["evaluated_on_synthetic_rows"] is False
    assert crit["validation"]["augmentation_in_training_folds_only"] is True
    assert crit["evaluation"]["at_threshold"]["recall"] >= crit["evaluation"]["target_recall"]
    # The subgroup counts add up to the real cohort (sex splits it exactly).
    sex_n = sum(v["n"] for k, v in crit["bias_by_subgroup"].items() if k.startswith("sex="))
    age_n = sum(v["n"] for k, v in crit["bias_by_subgroup"].items() if k.startswith("age="))
    assert sex_n == age_n == 192


def test_augmentation_is_deterministic_and_never_copies_a_real_row() -> None:
    spec, X, y, _ = _spec_and_data()
    first, labels = augment(X, y, spec, seed=7)
    again, _ = augment(X, y, spec, seed=7)
    assert np.array_equal(first, again)
    assert len(first) == len(labels) > 0
    real = {tuple(row) for row in X}
    assert not any(tuple(row) in real for row in first)
    names = spec.names
    assert (first[:, names.index("sbp")] > first[:, names.index("dbp")]).all()


def test_each_fold_trains_on_augmented_rows_but_scores_only_real_ones() -> None:
    spec, X, y, groups = _spec_and_data()
    lr = (compare.model_by_key("lr"),)
    oof = compare.out_of_fold(
        X, y, groups=groups, spec=spec, use_augmentation=True, models=lr, repeats=2
    )
    assert oof.probs["lr"].shape == (2, len(y))  # one score per REAL row
    assert (oof.probs["lr"] > 0).all()


def test_same_seed_gives_same_artifacts(tmp_path) -> None:
    first = train.main(train.DATA_DIR, tmp_path / "a", repeats=1)
    second = train.main(train.DATA_DIR, tmp_path / "b", repeats=1)
    assert _without_timings(first) == _without_timings(second)
