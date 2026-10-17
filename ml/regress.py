"""Continuous target for the cleaning task: minutes until the bed is ready.

Three regressors are scored with the same repeated k-fold protocol as the
classifiers, each one as ``scale -> impute -> model`` with the explicit KNN
imputer fitted on the training part of every fold:

* a baseline that always predicts the training mean,
* linear regression (the simplest model that reads a continuous target),
* one tree-based regressor (random forest).

MAE (minutes) and R2 come from the scored folds only. The best MAE wins unless
another model is within one standard error of it, in which case the simplest of
the tied models wins (same one-standard-error rule as the classifiers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from compare import DEFAULT_IMPUTER, FOLDS, SEED, make_imputer


@dataclass(frozen=True)
class RegressorDef:
    key: str
    code: str
    label: str
    build: Callable[[int], Any]
    rank: int  # 1 = simplest
    servable: bool = True

    def make(self, seed: int, imputer: str = DEFAULT_IMPUTER) -> Pipeline:
        return Pipeline(
            [
                ("scale", StandardScaler()),
                ("impute", make_imputer(imputer)),
                ("model", self.build(seed)),
            ]
        )


REGRESSORS: tuple[RegressorDef, ...] = (
    RegressorDef("mean", "mean", "Línea base (promedio)", lambda seed: DummyRegressor(), 0, False),
    RegressorDef("linear", "lin", "Regresión lineal", lambda seed: LinearRegression(), 1),
    RegressorDef(
        "forest",
        "rfr",
        "Random forest (regresión)",
        lambda seed: RandomForestRegressor(
            n_estimators=200, min_samples_leaf=3, random_state=seed, n_jobs=1
        ),
        2,
    ),
)
REGRESSORS_BY_KEY = {r.key: r for r in REGRESSORS}


def compare_regressors(
    X: np.ndarray, y: np.ndarray, *, repeats: int, imputer: str = DEFAULT_IMPUTER
) -> dict[str, Any]:
    """Cross-validated MAE and R2 of every regressor, and the one to serve."""

    scores: dict[str, dict[str, list[float]]] = {
        r.key: {"mae": [], "r2": []} for r in REGRESSORS
    }
    for repeat in range(repeats):
        splitter = KFold(n_splits=FOLDS, shuffle=True, random_state=SEED + repeat)
        for train, test in splitter.split(X):
            for regressor in REGRESSORS:
                fitted = regressor.make(SEED, imputer).fit(X[train], y[train])
                predicted = fitted.predict(X[test])
                scores[regressor.key]["mae"].append(float(mean_absolute_error(y[test], predicted)))
                scores[regressor.key]["r2"].append(float(r2_score(y[test], predicted)))

    entries: list[dict[str, Any]] = []
    for regressor in REGRESSORS:
        mae, r2 = scores[regressor.key]["mae"], scores[regressor.key]["r2"]
        entries.append(
            {
                "id": regressor.key,
                "nombre": regressor.label,
                "mae": round(float(np.mean(mae)), 2),
                "mae_std": round(float(np.std(mae, ddof=1)), 2),
                "mae_se": round(float(np.std(mae, ddof=1) / np.sqrt(len(mae))), 2),
                "r2": round(float(np.mean(r2)), 3),
                "r2_std": round(float(np.std(r2, ddof=1)), 3),
                "simplicity_rank": regressor.rank,
            }
        )
    candidates = [e for e in entries if REGRESSORS_BY_KEY[e["id"]].servable]
    best = min(candidates, key=lambda e: e["mae"])
    tied = [e for e in candidates if e["mae"] <= best["mae"] + best["mae_se"]]
    winner = min(tied, key=lambda e: (e["simplicity_rank"], e["mae"]))
    for entry in entries:
        entry["winner"] = entry["id"] == winner["id"]
    baseline = next(e for e in entries if e["id"] == "mean")
    note = (
        f"{winner['nombre']} tiene el menor error absoluto medio (MAE) entre los modelos "
        f"servibles"
        if winner["id"] == best["id"]
        else f"{best['nombre']} tiene el MAE más bajo ({best['mae']:.2f}), pero "
        f"{winner['nombre']} ({winner['mae']:.2f}) está a menos de un error estándar "
        f"({best['mae_se']:.2f}) y es más simple"
    )
    return {
        "target": "minutes_to_ready",
        "protocol": {"repeats": repeats, "folds": FOLDS, "imputation": imputer, "seed": SEED},
        "models": entries,
        "winner": winner["id"],
        "decision": note,
        "improvement_over_baseline_minutes": round(baseline["mae"] - winner["mae"], 2),
    }
