"""Compare eight supervised models per task, then write the winner and ``metrics.json``.

Run at image build time (``RUN python train.py``) so the container carries
exactly the models its metrics describe. Everything is deterministic (seed 2026)
except the wall-clock fit times.

Method, in short (details and the winner rule live in ``compare.py``):

* **Criticality.** 192 real triage rows (MIMIC-IV-ED Demo). Evaluation is
  repeated stratified group k-fold on REAL rows only (all visits of one patient
  stay in the same fold). The within-cluster augmentation is applied to the
  training part of each fold and never to the evaluation part. The threshold of
  each model is the highest one whose out-of-fold recall reaches 0.85.
* **Cleaning delay.** 800 simulated events, repeated stratified k-fold, same
  eight models, threshold for out-of-fold recall >= 0.80.

The served model of each task is the winner of its comparison, fitted on all rows
(plus augmentation for criticality). ``REPORTE_MODELOS.md`` is generated from the
same run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np

import compare
import report
from augment import MULTIPLIER
from compare import confusion, mean_auc, mean_rates
from explain import importance_items, importance_reading, odds_ratios
from features import CLEANING_SPEC, CRITICALITY_SPEC, FeatureSpec

SEED = compare.SEED
FOLDS = compare.FOLDS
REPEATS = compare.REPEATS
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
ARTIFACT_DIR = HERE / "artifacts"

CRITICALITY_TARGET_RECALL = 0.85
USE_AUGMENTATION = True
CLEANING_TARGET_RECALL = 0.80
AGE_BANDS = (("<45", 0, 44), ("45-64", 45, 64), (">=65", 65, 200))


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def load_criticality(path: Path) -> tuple[list[dict[str, object]], np.ndarray, np.ndarray]:
    rows = read_csv(path)
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            name: float(row[name]) for name in CRITICALITY_SPEC.numeric
        }
        record["sex"] = "male" if row["sex"] == "M" else "female"
        record["admitted"] = int(row["admitted"])
        records.append(record)
    y = np.array([r["admitted"] for r in records], dtype=int)
    groups = np.array([int(r["group"]) for r in rows])
    return records, y, groups


def load_cleaning(path: Path) -> tuple[list[dict[str, object]], np.ndarray]:
    records: list[dict[str, object]] = []
    for row in read_csv(path):
        record: dict[str, object] = {
            name: float(row[name]) for name in CLEANING_SPEC.numeric
        }
        for name in CLEANING_SPEC.categorical:
            record[name] = row[name]
        record["late"] = int(row["late"])
        records.append(record)
    return records, np.array([r["late"] for r in records], dtype=int)


# ---------------------------------------------------------------------------
# Reporting blocks
# ---------------------------------------------------------------------------


def subgroup_table(
    y: np.ndarray,
    probs: np.ndarray,
    threshold: float,
    masks: dict[str, np.ndarray],
) -> dict[str, dict[str, object]]:
    table: dict[str, dict[str, object]] = {}
    for label, mask in masks.items():
        if mask.sum() == 0:
            continue
        ys, ps = y[mask], probs[:, mask]
        entry: dict[str, object] = {
            "n": int(mask.sum()),
            "n_positive": int(ys.sum()),
            "positive_rate": round(float(ys.mean()), 3),
        }
        entry.update(mean_rates(ys, ps, threshold))
        auc = mean_auc(ys, ps)
        entry["auc"] = auc["mean"] if auc else None
        flagged = np.mean([(p >= threshold).mean() for p in ps])
        entry["flagged_rate"] = round(float(flagged), 3)
        table[label] = entry
    return table


def evaluation_block(
    y: np.ndarray, probs: np.ndarray, threshold: float, target_recall: float
) -> dict[str, object]:
    pooled = np.mean([confusion(y, p >= threshold)["tp"] for p in probs])
    return {
        "auc": mean_auc(y, probs),
        "threshold": threshold,
        "target_recall": target_recall,
        "at_threshold": mean_rates(y, probs, threshold),
        "flagged_rate": round(float(np.mean([(p >= threshold).mean() for p in probs])), 3),
        "mean_true_positives_per_repeat": round(float(pooled), 1),
        "baseline_flag_everyone": {
            "recall": 1.0,
            "precision": round(float(y.mean()), 3),
        },
    }


def coefficient_table(pipeline: Any, spec: FeatureSpec) -> list[dict[str, object]]:
    coefs = pipeline.named_steps["model"].coef_[0]
    rows = [
        {"feature": name, "coefficient": round(float(c), 3)}
        for name, c in zip(spec.names, coefs)
    ]
    return sorted(rows, key=lambda r: -abs(float(r["coefficient"])))


def _importance_block(
    comparison: dict[str, Any], importance: dict[str, dict[str, float]]
) -> dict[str, Any]:
    """Permutation importance per model and the one that is highlighted."""

    by_model = {key: importance_items(drops) for key, drops in importance.items()}
    entries = {e["id"]: e for e in comparison["models"]}
    winner = comparison["winner"]
    tree_keys = [k for k in by_model if compare.model_by_key(k).tree_based]
    if compare.model_by_key(winner).tree_based:
        shown = winner
    else:
        shown = max(tree_keys, key=lambda k: entries[k]["f1"] or 0.0)
    return {
        "method": (
            "Importancia por permutación sobre filas fuera de muestra: caída del AUC al "
            "mezclar una variable (se promedia en los folds de la primera repetición)."
        ),
        "shown_model": shown,
        "shown_model_name": entries[shown]["nombre"],
        "reading": importance_reading(by_model[shown]),
        "by_model": by_model,
    }


# ---------------------------------------------------------------------------
# One task
# ---------------------------------------------------------------------------


def _run_task(
    *,
    prefix: str,
    artifact_name: str,
    spec: FeatureSpec,
    X: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray | None,
    use_augmentation: bool,
    target_recall: float,
    positive_label: str,
    negative_label: str,
    positive_text: str,
    digest: str,
    repeats: int,
    out_dir: Path,
    masks: dict[str, np.ndarray],
) -> dict[str, Any]:
    started = time.perf_counter()
    comparison, oof = compare.compare_models(
        X,
        y,
        groups=groups,
        spec=spec,
        use_augmentation=use_augmentation,
        target_recall=target_recall,
        repeats=repeats,
    )
    winner = compare.model_by_key(comparison["winner"])
    entry = next(e for e in comparison["models"] if e["id"] == winner.key)
    probs = oof.probs[winner.key]
    threshold = float(entry["threshold"])

    estimator, params, n_synthetic = compare.fit_model(
        winner,
        X,
        y,
        groups=groups,
        spec=spec,
        use_augmentation=use_augmentation,
        seed=SEED,
    )
    logistic, _, _ = compare.fit_model(
        compare.model_by_key("lr"),
        X,
        y,
        groups=groups,
        spec=spec,
        use_augmentation=use_augmentation,
        seed=SEED,
    )

    ablation = None
    if use_augmentation:
        plain = compare.out_of_fold(
            X,
            y,
            groups=groups,
            spec=spec,
            use_augmentation=False,
            models=(winner,),
            repeats=repeats,
            importance=False,
        )
        ablation = {
            "model": winner.key,
            "auc_with_augmentation": mean_auc(y, probs)["mean"],  # type: ignore[index]
            "auc_without_augmentation": mean_auc(y, plain.probs[winner.key])["mean"],  # type: ignore[index]
        }

    comparison["importance"] = _importance_block(comparison, oof.importance)
    comparison["odds_ratios"] = {
        "model": "lr",
        "model_name": compare.model_by_key("lr").label,
        "note": (
            "Regresión logística estandarizada (L2, C = 1) ajustada con todas las filas. Para "
            "una variable numérica, la razón de momios es exp(coeficiente): el cambio de los "
            "momios por una desviación estándar. Para una categoría, compara estar en ella "
            "frente a no estarlo. La regularización encoge un poco los coeficientes."
        ),
        "items": odds_ratios(logistic, spec, positive_text),
    }

    version = f"{prefix}-{winner.code}-1.0-{digest}"
    joblib.dump(
        {
            "name": artifact_name,
            "version": version,
            "family": winner.key,
            "explain": "linear" if winner.key == "lr" else "occlusion",
            "spec": spec,
            "estimator": estimator,
            "threshold": threshold,
            "positive_label": positive_label,
            "negative_label": negative_label,
        },
        out_dir / f"{artifact_name}.joblib",
    )

    block: dict[str, Any] = {
        "model_version": version,
        "algorithm": f"{winner.label}, hiperparámetros {params or 'sin ajuste'}",
        "winner": winner.key,
        "features": spec.names,
        "imputation_defaults": spec.defaults,
        "evaluation": evaluation_block(y, probs, threshold, target_recall),
        "bias_by_subgroup": subgroup_table(y, probs, threshold, masks),
        "coefficients": coefficient_table(logistic, spec),
        "comparacion": comparison,
        "synthetic_rows_in_final_fit": n_synthetic,
        "training_seconds": round(time.perf_counter() - started, 1),
    }
    if ablation:
        block["ablation"] = ablation
    return block


def train_criticality(data_dir: Path, out_dir: Path, repeats: int) -> dict[str, object]:
    path = data_dir / "criticality_triage.csv"
    records, y, groups = load_criticality(path)
    spec = FeatureSpec(CRITICALITY_SPEC.numeric, dict(CRITICALITY_SPEC.categorical))
    spec.fit_defaults(records)
    X = spec.matrix(records)

    age = np.array([r["age"] for r in records])
    sex = np.array([r["sex"] for r in records])
    masks: dict[str, np.ndarray] = {
        "sex=female": sex == "female",
        "sex=male": sex == "male",
    }
    for label, low, high in AGE_BANDS:
        masks[f"age={label}"] = (age >= low) & (age <= high)

    block = _run_task(
        prefix="criticality",
        artifact_name="criticality",
        spec=spec,
        X=X,
        y=y,
        groups=groups,
        use_augmentation=USE_AUGMENTATION,
        target_recall=CRITICALITY_TARGET_RECALL,
        positive_label="critico",
        negative_label="no_critico",
        positive_text="ser crítico",
        digest=file_digest(path),
        repeats=repeats,
        out_dir=out_dir,
        masks=masks,
    )
    return {
        "model_version": block.pop("model_version"),
        "task": "Patient needs admission with urgency (target: admitted from the ED).",
        "algorithm": block.pop("algorithm"),
        "data": {
            "source": "MIMIC-IV-ED Demo v2.2 + MIMIC-IV Demo v2.2 (ODbL 1.0)",
            "real_rows": int(len(y)),
            "patients": int(len(set(groups.tolist()))),
            "positive_rate": round(float(y.mean()), 3),
            "synthetic_rows_in_final_fit": block.pop("synthetic_rows_in_final_fit"),
            "augmentation_multiplier": MULTIPLIER,
        },
        "validation": {
            "scheme": f"{repeats}x repeated stratified group {FOLDS}-fold on REAL rows",
            "augmentation_in_training_folds_only": USE_AUGMENTATION,
            "evaluated_on_synthetic_rows": False,
        },
        **block,
    }


def train_cleaning(data_dir: Path, out_dir: Path, repeats: int) -> dict[str, object]:
    path = data_dir / "cleaning_events.csv"
    records, y = load_cleaning(path)
    spec = FeatureSpec(CLEANING_SPEC.numeric, dict(CLEANING_SPEC.categorical))
    spec.fit_defaults(records)
    X = spec.matrix(records)

    masks: dict[str, np.ndarray] = {}
    for column in ("service", "shift"):
        values = np.array([r[column] for r in records])
        for value in CLEANING_SPEC.categorical[column]:
            masks[f"{column}={value}"] = values == value

    block = _run_task(
        prefix="cleaning-delay",
        artifact_name="cleaning_delay",
        spec=spec,
        X=X,
        y=y,
        groups=None,
        use_augmentation=False,
        target_recall=CLEANING_TARGET_RECALL,
        positive_label="retraso",
        negative_label="a_tiempo",
        positive_text="retraso de limpieza",
        digest=file_digest(path),
        repeats=repeats,
        out_dir=out_dir,
        masks=masks,
    )
    return {
        "model_version": block.pop("model_version"),
        "task": "A bed that enters cleaning will take more than 90 minutes to be ready.",
        "algorithm": block.pop("algorithm"),
        "data": {
            "source": "Simulated events (tools/generate_cleaning_events.py, seed 2026)",
            "rows": int(len(y)),
            "positive_rate": round(float(y.mean()), 3),
        },
        "validation": {
            "scheme": f"{repeats}x repeated stratified {FOLDS}-fold",
            "evaluated_on_synthetic_rows": True,
            "note": "The whole dataset is simulated; the metrics describe the simulation.",
        },
        **block,
    }


def main(
    data_dir: Path = DATA_DIR, out_dir: Path = ARTIFACT_DIR, repeats: int = REPEATS
) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "seed": SEED,
        "criticality": train_criticality(data_dir, out_dir, repeats),
        "cleaning_delay": train_cleaning(data_dir, out_dir, repeats),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "REPORTE_MODELOS.md").write_text(report.render(metrics), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    args = parser.parse_args()
    result = main(args.data_dir, args.out_dir, args.repeats)
    for name in ("criticality", "cleaning_delay"):
        block = result[name]
        print(name, "winner", block["winner"], block["evaluation"]["threshold"], block["evaluation"]["at_threshold"])
        for entry in block["comparacion"]["models"]:
            print(
                "  ",
                entry["id"],
                "auc",
                entry["auc"]["mean"] if entry["auc"] else None,
                "f1",
                entry["f1"],
                "recall",
                entry["recall"],
                "s",
                entry["fit_seconds"],
            )
        print("  total seconds", block["training_seconds"])
