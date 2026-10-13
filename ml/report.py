"""Human-readable Spanish report generated from the same run as ``metrics.json``."""

from __future__ import annotations

from typing import Any

TASK_TITLES = {
    "criticality": "Criticidad del paciente",
    "cleaning_delay": "Retraso de limpieza de camas",
}
POSITIVE_NAMES = {
    "criticality": ("Críticos detectados", "Críticos perdidos", "Falsas alarmas", "No críticos descartados"),
    "cleaning_delay": (
        "Retrasos detectados",
        "Retrasos perdidos",
        "Falsas alarmas",
        "A tiempo descartados",
    ),
}
TOP = 5


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _auc(entry: dict[str, Any]) -> str:
    auc = entry["auc"]
    return "—" if not auc else f"{auc['mean']:.3f} ± {auc['std']:.3f}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _task(name: str, block: dict[str, Any]) -> list[str]:
    comparison = block["comparacion"]
    entries = comparison["models"]
    winner = next(e for e in entries if e["id"] == comparison["winner"])
    protocol = comparison["protocol"]
    detected, missed, false_alarm, discarded = POSITIVE_NAMES[name]
    rows_n = block["data"].get("real_rows") or block["data"].get("rows")

    out = [f"## {TASK_TITLES[name]}", ""]
    grouped = name == "criticality"
    out.append(
        f"- Datos: {rows_n} filas, tasa de positivos {_pct(block['data']['positive_rate'])}. "
        f"Validación: {protocol['repeats']} repeticiones de validación cruzada estratificada de "
        f"{protocol['folds']} folds"
        + (" agrupada por paciente (las visitas de un paciente quedan en el mismo fold)" if grouped else "")
        + "."
    )
    if block["validation"]["evaluated_on_synthetic_rows"]:
        out.append("- Los datos son simulados: las métricas describen la simulación, no hospitales reales.")
    else:
        out.append("- Se evalúa solo con filas reales; el aumento de datos se usa únicamente al entrenar cada fold.")
    out.append(
        f"- Ajuste de hiperparámetros dentro de cada fold de entrenamiento "
        f"({protocol['inner_folds_for_tuning']} folds internos, AUC). "
        f"Umbral elegido sobre predicciones fuera de muestra, sensibilidad mínima "
        f"{_pct(protocol['target_recall'])}."
    )
    out.append(f"- Regla del ganador: {comparison['rule']}")
    out.append("")

    out.append("### Comparación de modelos")
    out.append("")
    headers = [
        "Modelo",
        "AUC (media ± DE)",
        "F1",
        "Precisión",
        "Sensibilidad",
        "Especificidad",
        "Umbral",
        "TN",
        "FP",
        "FN",
        "TP",
        "Ajuste (s/fold)",
    ]
    rows = []
    for e in entries:
        c = e["confusion"]
        name_cell = f"**{e['nombre']} (ganador)**" if e["winner"] else e["nombre"]
        rows.append(
            [
                name_cell,
                _auc(e),
                _num(e["f1"]),
                _num(e["precision"]),
                _num(e["recall"]),
                _num(e["specificity"]),
                _num(e["threshold"], 3),
                f"{c['tn']:.1f}",
                f"{c['fp']:.1f}",
                f"{c['fn']:.1f}",
                f"{c['tp']:.1f}",
                f"{e['fit_seconds']:.1f}",
            ]
        )
    out.extend(_table(headers, rows))
    out.append("")
    out.append(
        f"Las métricas son el promedio de {protocol['repeats']} repeticiones de validación "
        f"cruzada de {protocol['folds']} folds; la matriz de confusión es el conteo promedio por "
        f"repetición (TN = verdaderos negativos, FP = falsos positivos, FN = falsos negativos, "
        f"TP = verdaderos positivos)."
    )
    out.append("")
    base = comparison["baseline_flag_everyone"]
    beaten = [e["nombre"] for e in entries if e["f1"] is not None and e["f1"] > base["f1"]]
    out.append(
        f"Línea base (marcar a todos): sensibilidad 100%, precisión {_pct(base['precision'])}, "
        f"F1 {_num(base['f1'])}. "
        + (
            f"La superan en F1: {', '.join(beaten)}."
            if beaten
            else "Ningún modelo la supera en F1: con tantos positivos, marcar a todos ya da "
            "un F1 alto; lo que aportan los modelos es especificidad (descartar casos), no F1."
        )
    )
    out.append("")
    best = max((e for e in entries if e["f1"] is not None), key=lambda e: e["f1"])
    out.append(
        f"El error estándar del F1 del mejor modelo ({best['nombre']}) es {_num(best['f1_se'])}: "
        f"diferencias de F1 menores que eso entre modelos son ruido."
    )
    out.append("")
    out.append(f"**Por qué ganó {winner['nombre']}:** {comparison['decision']}.")
    out.append("")

    c = winner["confusion"]
    out.append(f"### Matriz de confusión de {winner['nombre']}")
    out.append("")
    out.extend(
        _table(
            ["", "Marcado por el modelo", "No marcado"],
            [
                ["**Caso real**", f"{detected}: {c['tp']:.1f}", f"{missed}: {c['fn']:.1f}"],
                ["**No es caso**", f"{false_alarm}: {c['fp']:.1f}", f"{discarded}: {c['tn']:.1f}"],
            ],
        )
    )
    out.append("")
    out.append(
        f"F1 {_num(winner['f1'])}, precisión {_pct(winner['precision'])}, sensibilidad "
        f"{_pct(winner['recall'])}, especificidad {_pct(winner['specificity'])} con umbral "
        f"{_num(winner['threshold'], 3)}."
    )
    out.append("")

    importance = comparison["importance"]
    items = importance["by_model"][importance["shown_model"]][:TOP]
    out.append(f"### Importancia de variables ({importance['shown_model_name']})")
    out.append("")
    out.append(importance["method"])
    out.append("")
    out.extend(
        _table(
            ["Variable", "Caída del AUC"],
            [[i["label"], f"{i['importance']:.3f}"] for i in items],
        )
    )
    out.append("")
    out.append(importance["reading"])
    out.append("")

    ratios = comparison["odds_ratios"]
    out.append(f"### Razones de momios ({ratios['model_name']})")
    out.append("")
    out.append(ratios["note"])
    out.append("")
    out.extend(
        _table(
            ["Variable", "Razón de momios", "Lectura"],
            [[i["label"], f"{i['odds_ratio']:.2f}", i["reading"]] for i in ratios["items"][:TOP]],
        )
    )
    out.append("")
    if "ablation" in block:
        a = block["ablation"]
        out.append(
            f"Aumento de datos sintéticos en el entrenamiento ({winner['nombre']}): AUC "
            f"{a['auc_with_augmentation']:.3f} con aumento y {a['auc_without_augmentation']:.3f} "
            f"sin aumento."
        )
        out.append("")
    return out


def render(metrics: dict[str, Any]) -> str:
    lines = [
        "# Reporte de modelos de IA de SaludRed",
        "",
        "Generado por `ml/train.py` en cada construcción de la imagen; los números salen de "
        "la misma corrida que `metrics.json` y de los modelos que atiende el servicio. "
        "Semilla fija: " + str(metrics["seed"]) + ".",
        "",
        "Modelos comparados: regresión logística, KNN, SVM con kernel RBF, árbol de decisión, "
        "random forest (bagging), gradient boosting de scikit-learn, stacking (logística + "
        "random forest + boosting con meta-modelo logístico) y una red neuronal pequeña (MLP).",
        "",
        "Limitaciones que aplican a todo el reporte: la cohorte de criticidad es una muestra "
        "pequeña (demo de MIMIC-IV-ED); el umbral se elige con las mismas predicciones fuera de "
        "muestra con que se reporta, por lo que la sensibilidad objetivo se cumple por "
        "construcción; los modelos apoyan la priorización y no sustituyen la decisión clínica.",
        "",
    ]
    for name in ("criticality", "cleaning_delay"):
        lines.extend(_task(name, metrics[name]))
    return "\n".join(lines).rstrip() + "\n"
