"""Model comparison with one protocol for every model and both tasks.

Eight supervised models are trained, tuned and scored the same way:

* Evaluation is repeated stratified k-fold (grouped by patient for criticality)
  on REAL rows only. The cluster augmentation, when used, is applied to the
  training part of each fold and never to the part that is scored.
* Hyperparameters are tuned INSIDE each training fold (nested CV, 3 inner
  folds, ROC AUC), so the fold that is scored never influences the tuning.
* The decision threshold of each model is chosen on the out-of-fold
  predictions: the highest threshold whose out-of-fold recall reaches the
  target of the task. It is never chosen on a model fitted on all the data.
* Winner rule: among the models that meet the recall target, the best
  out-of-fold F1; models within one standard error of the best F1 (taken over
  the outer folds) are a practical tie and the simplest / most explainable one
  wins (the one-standard-error rule).
"""

from __future__ import annotations

import time
import warnings
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sklearn.ensemble import (
    GradientBoostingClassifier,
    RandomForestClassifier,
    StackingClassifier,
)
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import ParameterGrid, StratifiedGroupKFold, StratifiedKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

from augment import augment
from features import FeatureSpec

SEED = 2026
FOLDS = 5
REPEATS = 3
INNER_FOLDS = 3
PERMUTATION_REPEATS = 5

warnings.filterwarnings("ignore", category=ConvergenceWarning)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def confusion(y: np.ndarray, flagged: np.ndarray) -> dict[str, int]:
    return {
        "tp": int(((y == 1) & flagged).sum()),
        "fp": int(((y == 0) & flagged).sum()),
        "fn": int(((y == 1) & ~flagged).sum()),
        "tn": int(((y == 0) & ~flagged).sum()),
    }


def rates(y: np.ndarray, prob: np.ndarray, threshold: float) -> dict[str, float | None]:
    c = confusion(y, prob >= threshold)
    recall = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else None
    precision = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else None
    specificity = c["tn"] / (c["tn"] + c["fp"]) if (c["tn"] + c["fp"]) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    return {"recall": recall, "precision": precision, "specificity": specificity, "f1": f1}


def pick_threshold(y: np.ndarray, probs: np.ndarray, target_recall: float) -> float:
    """Highest threshold whose mean out-of-fold recall still reaches the target."""

    grid = np.round(np.arange(0.01, 0.99, 0.005), 3)
    best = float(grid[0])
    for threshold in grid:
        recall = np.mean([rates(y, p, threshold)["recall"] for p in probs])
        if recall >= target_recall:
            best = float(threshold)
    return best


def mean_rates(y: np.ndarray, probs: np.ndarray, threshold: float) -> dict[str, float | None]:
    per_repeat = [rates(y, p, threshold) for p in probs]
    out: dict[str, float | None] = {}
    for key in per_repeat[0]:
        values = [r[key] for r in per_repeat if r[key] is not None]
        out[key] = round(float(np.mean(values)), 3) if values else None
    return out


def mean_auc(y: np.ndarray, probs: np.ndarray) -> dict[str, float] | None:
    if len(set(y.tolist())) < 2:
        return None
    scores = [roc_auc_score(y, p) for p in probs]
    return {"mean": round(float(np.mean(scores)), 3), "std": round(float(np.std(scores)), 3)}


def mean_confusion(y: np.ndarray, probs: np.ndarray, threshold: float) -> dict[str, float]:
    per_repeat = [confusion(y, p >= threshold) for p in probs]
    return {key: round(float(np.mean([c[key] for c in per_repeat])), 1) for key in per_repeat[0]}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelDef:
    key: str
    code: str  # short family code used in ``model_version``
    label: str
    family: str
    build: Callable[[int], Any]
    grid: dict[str, list[Any]]
    rank: int  # 1 = simplest / most explainable (tie-break)
    tree_based: bool = False


def _scaled(model: Any) -> Pipeline:
    return Pipeline([("scale", StandardScaler()), ("model", model)])


def _logistic(seed: int) -> Pipeline:
    return _scaled(LogisticRegression(max_iter=2000, random_state=seed))


def _forest(seed: int, trees: int = 200) -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=trees, min_samples_leaf=3, random_state=seed, n_jobs=1
    )


def _boosting(seed: int, trees: int = 100) -> GradientBoostingClassifier:
    return GradientBoostingClassifier(
        n_estimators=trees, max_depth=2, learning_rate=0.1, random_state=seed
    )


def _stacking(seed: int) -> StackingClassifier:
    return StackingClassifier(
        estimators=[
            ("lr", _logistic(seed)),
            ("rf", _forest(seed, trees=100)),
            ("gb", _boosting(seed, trees=100)),
        ],
        final_estimator=LogisticRegression(max_iter=2000, random_state=seed),
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=seed),
        stack_method="predict_proba",
        n_jobs=1,
    )


# The grids are small on purpose: they keep the image build under a few minutes.
# They are ordered from the simplest to the most flexible setting; a tie in the
# inner score keeps the simplest one.
MODELS: tuple[ModelDef, ...] = (
    ModelDef("lr", "lr", "Regresión logística", "lineal", _logistic, {}, 1),
    ModelDef(
        "tree",
        "dt",
        "Árbol de decisión",
        "árbol",
        lambda seed: DecisionTreeClassifier(min_samples_leaf=5, random_state=seed),
        {"max_depth": [2, 3, 4, 5, 6]},
        2,
        tree_based=True,
    ),
    ModelDef(
        "knn",
        "knn",
        "KNN",
        "distancia",
        lambda seed: _scaled(KNeighborsClassifier()),
        {"model__n_neighbors": [25, 15, 9, 5, 3]},
        3,
    ),
    ModelDef(
        "rf",
        "rf",
        "Random forest (bagging)",
        "bagging",
        _forest,
        {},
        4,
        tree_based=True,
    ),
    ModelDef(
        "gb",
        "gb",
        "Gradient boosting",
        "boosting",
        _boosting,
        {"n_estimators": [50, 100, 200]},
        5,
        tree_based=True,
    ),
    ModelDef(
        "svm",
        "svm",
        "SVM (kernel RBF)",
        "kernel",
        lambda seed: _scaled(SVC(kernel="rbf", probability=True, random_state=seed)),
        {"model__C": [0.25, 1.0, 4.0]},
        6,
    ),
    ModelDef(
        "mlp",
        "mlp",
        "Red neuronal (MLP)",
        "red neuronal",
        lambda seed: _scaled(
            MLPClassifier(hidden_layer_sizes=(16,), max_iter=400, random_state=seed)
        ),
        {"model__alpha": [1.0, 0.01]},
        7,
    ),
    ModelDef("stack", "stack", "Stacking (LR + RF + GB)", "ensamble", _stacking, {}, 8),
)
MODELS_BY_KEY = {m.key: m for m in MODELS}


def model_by_key(key: str) -> ModelDef:
    return MODELS_BY_KEY[key]


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------

Inner = list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


def _with_augmentation(
    X: np.ndarray, y: np.ndarray, spec: FeatureSpec, seed: int, enabled: bool
) -> tuple[np.ndarray, np.ndarray]:
    if not enabled:
        return X, y
    X_syn, y_syn = augment(X, y, spec, seed=seed)
    if len(y_syn):
        return np.vstack([X, X_syn]), np.concatenate([y, y_syn])
    return X, y


def inner_folds(
    X: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray | None,
    spec: FeatureSpec,
    use_augmentation: bool,
    seed: int,
) -> Inner:
    """Inner splits for tuning. Validation rows are real; training rows may be augmented."""

    if groups is not None:
        splits = StratifiedGroupKFold(n_splits=INNER_FOLDS, shuffle=True, random_state=seed).split(
            X, y, groups
        )
    else:
        splits = StratifiedKFold(n_splits=INNER_FOLDS, shuffle=True, random_state=seed).split(X, y)
    folds: Inner = []
    for k, (train, valid) in enumerate(splits):
        X_tr, y_tr = _with_augmentation(X[train], y[train], spec, seed * 10 + k, use_augmentation)
        folds.append((X_tr, y_tr, X[valid], y[valid]))
    return folds


def tune(model: ModelDef, inner: Inner) -> dict[str, Any]:
    """Best parameters by mean inner ROC AUC; ties keep the earlier (simpler) setting."""

    if not model.grid:
        return {}
    best: dict[str, Any] | None = None
    best_score = -np.inf
    for params in ParameterGrid(model.grid):
        scores = []
        for X_tr, y_tr, X_va, y_va in inner:
            if len(set(y_va.tolist())) < 2:
                continue
            estimator = model.build(SEED).set_params(**params).fit(X_tr, y_tr)
            scores.append(roc_auc_score(y_va, estimator.predict_proba(X_va)[:, 1]))
        score = float(np.mean(scores)) if scores else -np.inf
        if best is None or score > best_score + 1e-9:
            best, best_score = dict(params), score
    return best or {}


def fit_model(
    model: ModelDef,
    X: np.ndarray,
    y: np.ndarray,
    *,
    groups: np.ndarray | None,
    spec: FeatureSpec,
    use_augmentation: bool,
    seed: int,
    inner: Inner | None = None,
) -> tuple[Any, dict[str, Any], int]:
    """Tune on ``X`` and fit on all of it. Returns (estimator, params, synthetic rows)."""

    if inner is None:
        inner = inner_folds(X, y, groups, spec, use_augmentation, seed)
    params = tune(model, inner)
    X_fit, y_fit = _with_augmentation(X, y, spec, seed, use_augmentation)
    estimator = model.build(SEED).set_params(**params).fit(X_fit, y_fit)
    return estimator, params, int(len(y_fit) - len(y))


def input_columns(spec: FeatureSpec) -> dict[str, list[int]]:
    """Encoded column indexes of every input (a categorical input spans a block)."""

    names = spec.names
    columns: dict[str, list[int]] = {}
    for name in spec.numeric:
        columns[name] = [names.index(name)]
    for name in spec.categorical:
        columns[name] = [i for i, column in enumerate(names) if column.startswith(f"{name}=")]
    return columns


def permutation_importance(
    estimator: Any, X: np.ndarray, y: np.ndarray, spec: FeatureSpec, seed: int
) -> dict[str, float]:
    """AUC drop when ONE input (its whole one-hot block) is shuffled, on held-out rows."""

    if len(set(y.tolist())) < 2:
        return {}
    rng = np.random.default_rng(seed)
    base = roc_auc_score(y, estimator.predict_proba(X)[:, 1])
    out: dict[str, float] = {}
    for name, columns in input_columns(spec).items():
        drops = []
        for _ in range(PERMUTATION_REPEATS):
            shuffled = X.copy()
            shuffled[:, columns] = X[rng.permutation(len(X))][:, columns]
            drops.append(base - roc_auc_score(y, estimator.predict_proba(shuffled)[:, 1]))
        out[name] = float(np.mean(drops))
    return out


# ---------------------------------------------------------------------------
# Out-of-fold evaluation
# ---------------------------------------------------------------------------


@dataclass
class OutOfFold:
    probs: dict[str, np.ndarray]  # key -> (repeats, rows)
    fit_seconds: dict[str, float]  # mean per outer fold, tuning included
    params: dict[str, list[dict[str, Any]]]  # key -> chosen parameters per outer fold
    importance: dict[str, dict[str, float]]  # key -> input -> mean AUC drop
    folds: list[tuple[int, np.ndarray]]  # (repeat, scored row indexes) of every outer fold


def out_of_fold(
    X: np.ndarray,
    y: np.ndarray,
    *,
    groups: np.ndarray | None,
    spec: FeatureSpec,
    use_augmentation: bool,
    models: tuple[ModelDef, ...] = MODELS,
    repeats: int = REPEATS,
    importance: bool = True,
) -> OutOfFold:
    """Out-of-fold probabilities, one row per repeat. Only REAL rows are scored."""

    probs = {m.key: np.zeros((repeats, len(y))) for m in models}
    seconds = {m.key: 0.0 for m in models}
    chosen: dict[str, list[dict[str, Any]]] = {m.key: [] for m in models}
    drops: dict[str, list[dict[str, float]]] = {m.key: [] for m in models}
    folds_run = 0
    scored: list[tuple[int, np.ndarray]] = []

    for repeat in range(repeats):
        seed = SEED + repeat
        if groups is not None:
            splitter = StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=seed)
            splits = splitter.split(X, y, groups)
        else:
            splitter = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=seed)
            splits = splitter.split(X, y)
        for fold, (train, test) in enumerate(splits):
            fold_seed = seed * 100 + fold
            inner = inner_folds(
                X[train],
                y[train],
                groups[train] if groups is not None else None,
                spec,
                use_augmentation,
                fold_seed,
            )
            folds_run += 1
            scored.append((repeat, test))
            for model in models:
                started = time.perf_counter()
                estimator, params, _ = fit_model(
                    model,
                    X[train],
                    y[train],
                    groups=None,
                    spec=spec,
                    use_augmentation=use_augmentation,
                    seed=fold_seed,
                    inner=inner,
                )
                seconds[model.key] += time.perf_counter() - started
                probs[model.key][repeat, test] = estimator.predict_proba(X[test])[:, 1]
                chosen[model.key].append(params)
                if importance and repeat == 0:
                    drops[model.key].append(
                        permutation_importance(estimator, X[test], y[test], spec, fold_seed)
                    )

    averaged: dict[str, dict[str, float]] = {}
    for key, per_fold in drops.items():
        if not per_fold:
            continue
        names = per_fold[0].keys()
        averaged[key] = {n: float(np.mean([d[n] for d in per_fold if n in d])) for n in names}
    return OutOfFold(
        probs=probs,
        fit_seconds={k: v / max(folds_run, 1) for k, v in seconds.items()},
        params=chosen,
        importance=averaged,
        folds=scored,
    )


# ---------------------------------------------------------------------------
# Comparison and winner
# ---------------------------------------------------------------------------


def summarize_params(per_fold: list[dict[str, Any]]) -> dict[str, Any]:
    """Most frequent value of every tuned parameter across the outer folds."""

    if not per_fold or not per_fold[0]:
        return {}
    out: dict[str, Any] = {}
    for name in per_fold[0]:
        value = Counter(str(p[name]) for p in per_fold).most_common(1)[0][0]
        out[name.split("__")[-1]] = float(value) if "." in value else int(value)
    return out


def model_entry(
    model: ModelDef,
    y: np.ndarray,
    probs: np.ndarray,
    *,
    target_recall: float,
    fit_seconds: float,
    params: list[dict[str, Any]],
    folds: list[tuple[int, np.ndarray]],
) -> dict[str, Any]:
    threshold = pick_threshold(y, probs, target_recall)
    at = mean_rates(y, probs, threshold)
    # Standard error of the F1 across the outer folds (they are scored at the model's own
    # threshold). It is the noise level used to decide whether two F1 values are a tie.
    fold_f1 = [
        rates(y[test], probs[repeat, test], threshold)["f1"] for repeat, test in folds
    ]
    fold_f1 = [v for v in fold_f1 if v is not None]
    f1_se = float(np.std(fold_f1, ddof=1) / np.sqrt(len(fold_f1))) if len(fold_f1) > 1 else 0.0
    return {
        "id": model.key,
        "nombre": model.label,
        "familia": model.family,
        "auc": mean_auc(y, probs),
        "threshold": threshold,
        "f1": at["f1"],
        "f1_se": round(f1_se, 3),
        "precision": at["precision"],
        "recall": at["recall"],
        "specificity": at["specificity"],
        "confusion": mean_confusion(y, probs, threshold),
        "meets_recall_target": bool(at["recall"] is not None and at["recall"] >= target_recall),
        "hyperparameters": summarize_params(params),
        "fit_seconds": round(fit_seconds, 2),
        "simplicity_rank": model.rank,
    }


def choose_winner(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (winner id, how it was decided) following the documented rule."""

    eligible = [e for e in entries if e["meets_recall_target"] and e["f1"] is not None]
    if not eligible:
        eligible = [e for e in entries if e["f1"] is not None]
        note = "ningún modelo alcanzó la sensibilidad objetivo; se tomó el mejor F1"
    else:
        note = "mejor F1 entre los modelos que cumplen la sensibilidad objetivo"
    top = max(eligible, key=lambda e: e["f1"])
    # One-standard-error rule: models whose F1 is within one standard error of the best
    # one cannot be told apart from it, so the simplest and most explainable of them wins.
    margin = float(top.get("f1_se", 0.0))
    tied = [e for e in eligible if e["f1"] >= top["f1"] - margin]
    winner = min(tied, key=lambda e: (e["simplicity_rank"], -e["f1"]))
    if winner["id"] != top["id"]:
        note += (
            f"; {top['nombre']} tiene el F1 más alto ({top['f1']:.3f}), pero la diferencia con "
            f"{winner['nombre']} ({winner['f1']:.3f}) es de {top['f1'] - winner['f1']:.3f}, "
            f"menor que un error estándar del F1 ({margin:.3f}): es un empate práctico y se "
            f"prefiere el modelo más simple y explicable"
        )
    return winner["id"], note


def rule_text(target_recall: float) -> str:
    return (
        f"El umbral de cada modelo es el más alto cuya sensibilidad fuera de muestra alcanza "
        f"{target_recall:.0%}. Gana el modelo con mayor F1 fuera de muestra entre los que "
        f"cumplen esa sensibilidad; si otro modelo queda a menos de un error estándar del F1 "
        f"del mejor, se considera un empate práctico y gana el más simple y explicable "
        f"(regresión logística, árbol, KNN, random forest, boosting, SVM, red neuronal, "
        f"stacking, en ese orden)."
    )


def compare_models(
    X: np.ndarray,
    y: np.ndarray,
    *,
    groups: np.ndarray | None,
    spec: FeatureSpec,
    use_augmentation: bool,
    target_recall: float,
    models: tuple[ModelDef, ...] = MODELS,
    repeats: int = REPEATS,
) -> tuple[dict[str, Any], OutOfFold]:
    oof = out_of_fold(
        X,
        y,
        groups=groups,
        spec=spec,
        use_augmentation=use_augmentation,
        models=models,
        repeats=repeats,
    )
    entries = [
        model_entry(
            m,
            y,
            oof.probs[m.key],
            target_recall=target_recall,
            fit_seconds=oof.fit_seconds[m.key],
            params=oof.params[m.key],
            folds=oof.folds,
        )
        for m in models
    ]
    prevalence = float(y.mean())
    baseline_f1 = 2 * prevalence / (1 + prevalence)
    winner, decided = choose_winner(entries)
    for entry in entries:
        entry["winner"] = entry["id"] == winner
    return (
        {
            "protocol": {
                "repeats": repeats,
                "folds": FOLDS,
                "inner_folds_for_tuning": INNER_FOLDS,
                "seed": SEED,
                "augmentation_in_training_folds_only": use_augmentation,
                "threshold_chosen_on": "out-of-fold predictions",
                "target_recall": target_recall,
            },
            "baseline_flag_everyone": {
                "recall": 1.0,
                "precision": round(prevalence, 3),
                "specificity": 0.0,
                "f1": round(baseline_f1, 3),
            },
            "models": entries,
            "winner": winner,
            "rule": rule_text(target_recall),
            "decision": decided,
        },
        oof,
    )
