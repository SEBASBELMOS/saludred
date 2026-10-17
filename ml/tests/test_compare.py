"""The comparison: every model, threshold out-of-fold, the winner rule, the report."""

from __future__ import annotations

import numpy as np

import compare
import train
from features import CLEANING_SPEC, FeatureSpec

EXPECTED = {"lr", "tree", "knn", "rf", "gb", "lgbm", "xgb", "svm", "mlp", "stack"}
TASKS = ("criticality", "cleaning_delay")


def _entry(
    key: str, f1: float, *, ok: bool = True, rank: int = 1, se: float = 0.0
) -> dict:
    return {
        "id": key,
        "nombre": key.upper(),
        "f1": f1,
        "f1_se": se,
        "meets_recall_target": ok,
        "simplicity_rank": rank,
    }


def test_every_model_is_compared_on_both_tasks(artifacts) -> None:
    _, metrics = artifacts
    for task in TASKS:
        comparison = metrics[task]["comparacion"]
        assert {m["id"] for m in comparison["models"]} == EXPECTED
        for entry in comparison["models"]:
            assert entry["auc"]["mean"] > 0.5
            assert set(entry["confusion"]) == {"tn", "fp", "fn", "tp"}
            assert entry["fit_seconds"] >= 0
            assert 0 <= entry["f1"] <= 1
        assert sum(1 for m in comparison["models"] if m["winner"]) == 1
        assert comparison["winner"] in EXPECTED
        assert comparison["rule"] and comparison["decision"]


def test_confusion_matrix_adds_up_to_the_rows(artifacts) -> None:
    _, metrics = artifacts
    sizes = {"criticality": 192, "cleaning_delay": 800}
    for task in TASKS:
        for entry in metrics[task]["comparacion"]["models"]:
            assert round(sum(entry["confusion"].values()), 1) == sizes[task]


def test_threshold_is_chosen_on_out_of_fold_predictions_only() -> None:
    records, y = train.load_cleaning(train.DATA_DIR / "cleaning_events.csv")
    spec = FeatureSpec(CLEANING_SPEC.numeric, dict(CLEANING_SPEC.categorical))
    spec.fit_defaults(records)
    X = spec.matrix(records)
    lr = (compare.model_by_key("lr"),)
    comparison, oof = compare.compare_models(
        X, y, groups=None, spec=spec, use_augmentation=False, target_recall=0.8,
        models=lr, repeats=1,
    )
    entry = comparison["models"][0]
    probs = oof.probs["lr"]
    # The reported threshold is exactly the rule applied to the out-of-fold scores...
    assert entry["threshold"] == compare.pick_threshold(y, probs, 0.8)
    # ...and every reported rate is computed from those same scores at that threshold.
    assert entry["recall"] == compare.mean_rates(y, probs, entry["threshold"])["recall"]
    assert entry["recall"] >= 0.8
    # One notch higher would break the recall target: it is the highest such threshold.
    assert compare.mean_rates(y, probs, entry["threshold"] + 0.01)["recall"] < 0.8 or (
        entry["threshold"] >= 0.97
    )


def test_every_out_of_fold_score_comes_from_a_model_that_never_saw_that_row() -> None:
    records, y = train.load_cleaning(train.DATA_DIR / "cleaning_events.csv")
    spec = FeatureSpec(CLEANING_SPEC.numeric, dict(CLEANING_SPEC.categorical))
    spec.fit_defaults(records)
    X = spec.matrix(records)
    tree = (compare.model_by_key("tree"),)
    oof = compare.out_of_fold(
        X, y, groups=None, spec=spec, use_augmentation=False, models=tree, repeats=1,
        importance=False,
    )
    # A depth-limited tree scored on rows it did not train on cannot be perfect.
    assert compare.mean_auc(y, oof.probs["tree"])["mean"] < 0.99
    assert oof.probs["tree"].shape == (1, len(y))


def test_winner_is_the_best_f1_among_models_that_meet_the_recall_target() -> None:
    winner, note = compare.choose_winner(
        [
            _entry("a", 0.70, ok=False, rank=1),
            _entry("b", 0.55, rank=2),
            _entry("c", 0.60, rank=3),
        ]
    )
    assert winner == "c"
    assert "mejor F1" in note


def test_a_practical_tie_goes_to_the_simplest_model() -> None:
    # The best F1 has a standard error of 0.02: 0.625 is within it, 0.600 is not.
    winner, note = compare.choose_winner(
        [
            _entry("stack", 0.640, rank=8, se=0.02),
            _entry("lr", 0.625, rank=1, se=0.02),
            _entry("tree", 0.600, rank=2, se=0.02),
        ]
    )
    assert winner == "lr"
    assert "empate práctico" in note


def test_a_clear_gap_is_not_a_tie() -> None:
    winner, _ = compare.choose_winner(
        [_entry("lr", 0.50, rank=1, se=0.02), _entry("rf", 0.60, rank=4, se=0.02)]
    )
    assert winner == "rf"


def test_every_model_reports_its_f1_standard_error_and_the_baseline(artifacts) -> None:
    _, metrics = artifacts
    for task in TASKS:
        comparison = metrics[task]["comparacion"]
        assert all(m["f1_se"] >= 0 for m in comparison["models"])
        baseline = comparison["baseline_flag_everyone"]
        assert baseline["recall"] == 1.0 and 0 < baseline["f1"] < 1


def test_importance_and_odds_ratios_are_reported(artifacts) -> None:
    _, metrics = artifacts
    for task in TASKS:
        comparison = metrics[task]["comparacion"]
        importance = comparison["importance"]
        assert importance["reading"]
        shown = importance["by_model"][importance["shown_model"]]
        assert compare.model_by_key(importance["shown_model"]).tree_based
        assert shown == sorted(shown, key=lambda i: -i["importance"])
        assert {"rf", "tree", "gb"} <= set(importance["by_model"])
        ratios = comparison["odds_ratios"]["items"]
        assert ratios and all(r["odds_ratio"] > 0 and r["reading"] for r in ratios)
        # For a numeric input the odds ratio is exp(coefficient), per standard deviation.
        numeric = next(r for r in ratios if "=" not in r["feature"])
        assert abs(numeric["odds_ratio"] - float(np.exp(numeric["coefficient"]))) < 0.01
        # Its direction always agrees with the sign of the coefficient.
        assert all(
            (r["odds_ratio"] >= 1) == (r["coefficient"] >= 0)
            for r in ratios
            if abs(r["coefficient"]) > 0.01
        )


def _cleaning_data():
    records, y = train.load_cleaning(train.DATA_DIR / "cleaning_events.csv")
    spec = FeatureSpec(CLEANING_SPEC.numeric, dict(CLEANING_SPEC.categorical))
    spec.fit_defaults(records)
    return spec, spec.matrix(records), y


def test_svm_grid_tunes_c_and_gamma_together() -> None:
    grid = compare.model_by_key("svm").grid
    assert set(grid) == {"model__C", "model__gamma"}
    assert len(grid["model__C"]) >= 3 and len(grid["model__gamma"]) >= 3
    from sklearn.model_selection import ParameterGrid

    # The tuner walks the full product, not one axis at a time.
    assert len(ParameterGrid(grid)) == len(grid["model__C"]) * len(grid["model__gamma"])


def test_reported_svm_hyperparameters_include_gamma(artifacts) -> None:
    _, metrics = artifacts
    for task in TASKS:
        svm = next(m for m in metrics[task]["comparacion"]["models"] if m["id"] == "svm")
        assert {"C", "gamma"} <= set(svm["hyperparameters"])


def test_boosting_libraries_are_in_the_comparison(artifacts) -> None:
    _, metrics = artifacts
    for task in TASKS:
        names = {m["nombre"] for m in metrics[task]["comparacion"]["models"]}
        assert {"LightGBM", "XGBoost"} <= names


def test_knn_imputer_is_fitted_on_training_rows_only() -> None:
    spec, X, y = _cleaning_data()
    train_rows, test_rows = X[:600], X[600:].copy()
    test_rows[::2, spec.names.index("pending_cleaning_beds")] = np.nan
    pipeline = compare.model_by_key("lr").make(1).fit(train_rows, y[:600])
    imputer = pipeline.named_steps["impute"]
    assert type(imputer).__name__ == "KNNImputer"
    # The imputer only holds the (scaled) training rows: it never saw the held-out ones.
    assert imputer._fit_X.shape[0] == 600
    scaled_train = pipeline.named_steps["scale"].transform(train_rows)
    assert np.allclose(imputer._fit_X, scaled_train)
    # Rows with gaps are scored without error and without NaN in the output.
    assert np.isfinite(pipeline.predict_proba(test_rows)).all()


def test_median_imputer_is_available_for_the_ablation() -> None:
    pipeline = compare.model_by_key("lr").make(1, "median")
    assert type(pipeline.named_steps["impute"]).__name__ == "SimpleImputer"


def test_imputation_ablation_runs_both_imputers_with_the_same_protocol() -> None:
    spec, X, y = _cleaning_data()
    result = compare.imputation_ablation(
        X, y, groups=None, spec=spec, model=compare.model_by_key("lr"),
        target_recall=0.8, repeats=1,
    )
    assert set(result["arms"]) == {"knn", "median"}
    assert 0.1 < result["missing_rate_injected"] < 0.2
    assert result["chosen"] in {"knn", "median"} and result["reason"]


def test_metrics_report_the_imputation_ablation_and_the_regression(artifacts) -> None:
    out, metrics = artifacts
    for task in TASKS:
        imputation = metrics[task]["imputation"]
        assert imputation["training_rows_with_missing"] == 0
        assert imputation["method_served"] == imputation["chosen"]
    regression = metrics["cleaning_delay"]["regresion"]
    assert {m["id"] for m in regression["models"]} == {"mean", "linear", "forest"}
    by_id = {m["id"]: m for m in regression["models"]}
    assert by_id["linear"]["mae"] < by_id["mean"]["mae"]
    assert all(m["mae"] > 0 and m["r2"] <= 1 for m in regression["models"])
    text = (out / "REPORTE_MODELOS.md").read_text(encoding="utf-8")
    assert "mediana frente a KNN" in text and "Qué se aplicó de la clase del 06/10" in text


def test_report_is_generated_from_the_same_run(artifacts) -> None:
    out, metrics = artifacts
    text = (out / "REPORTE_MODELOS.md").read_text(encoding="utf-8")
    for task in TASKS:
        for entry in metrics[task]["comparacion"]["models"]:
            assert entry["nombre"] in text
    assert "Matriz de confusión" in text and "Razones de momios" in text
