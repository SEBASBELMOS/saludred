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

    svm = next((e for e in entries if e["id"] == "svm"), None)
    if svm:
        hp = svm["hyperparameters"]
        out.append(
            f"**SVM, C y gamma juntos:** se ajustaron en una sola malla (C en 0.25, 1, 4 y gamma "
            f"en «scale», 0.01, 0.1). El valor más elegido en los folds fue C = {hp.get('C')} y "
            f"gamma = {hp.get('gamma')}; AUC {_auc(svm)}, F1 {_num(svm['f1'])}."
        )
        out.append("")
    boosters = [e for e in entries if e["id"] in {"gb", "lgbm", "xgb"}]
    out.append(
        "**Boosting:** "
        + "; ".join(f"{e['nombre']} AUC {_auc(e)}, F1 {_num(e['f1'])}" for e in boosters)
        + "."
    )
    out.append("")
    out.extend(_imputation(block["imputation"], block["data"]))

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
    if "regresion" in block:
        out.extend(_regression(block["regresion"]))
    return out


def _imputation(imp: dict[str, Any], data: dict[str, Any]) -> list[str]:
    knn, median = imp["arms"]["knn"], imp["arms"]["median"]
    out = ["### Imputación de valores faltantes: mediana frente a KNN", ""]
    out.append(
        f"Filas de entrenamiento con algún valor faltante: {imp['training_rows_with_missing']} "
        f"({imp['training_missing_cells']} celdas). "
        + (
            "El conjunto de entrenamiento está completo: con él ambos imputadores darían "
            "exactamente lo mismo, así que la comparación se hizo borrando al azar "
            f"{_pct(imp['missing_rate_injected'])} de las celdas numéricas (mismas celdas para "
            "los dos métodos, sin aumento de datos porque no puede interpolar vacíos). "
            "En el servicio los faltantes sí aparecen (por ejemplo, un paciente sin nivel de triage)."
            if imp["training_rows_with_missing"] == 0
            else ""
        )
    )
    out.append("")
    out.extend(
        _table(
            ["Imputación", "AUC", "F1", "Error estándar del F1", "Sensibilidad", "Precisión"],
            [
                [
                    "Mediana" if kind == "median" else f"KNN (k = {imp['knn_neighbors']})",
                    _num(arm["auc"]),
                    _num(arm["f1"]),
                    _num(arm["f1_se"]),
                    _num(arm["recall"]),
                    _num(arm["precision"]),
                ]
                for kind, arm in (("median", median), ("knn", knn))
            ],
        )
    )
    out.append("")
    out.append(
        f"Modelo usado en la comparación: {imp['model_name']} (el ganador). Mismo protocolo "
        f"para ambos: el imputador se ajusta solo con las filas de entrenamiento de cada fold y "
        f"se aplica después de estandarizar. **Decisión:** {imp['reason']}. "
        f"Imputación en servicio: {'KNN' if imp['method_served'] == 'knn' else 'mediana'}."
    )
    out.append("")
    return out


def _regression(reg: dict[str, Any]) -> list[str]:
    protocol = reg["protocol"]
    target = reg["target_summary"]
    out = ["### Regresión: minutos hasta que la cama queda lista", ""]
    out.append(
        f"Variable continua `minutes_to_ready` (media {target['mean_minutes']} min, mediana "
        f"{target['median_minutes']} min, rango {target['min_minutes']}-{target['max_minutes']}). "
        f"Mismas variables de entrada, imputación KNN dentro del fold y {protocol['repeats']} "
        f"repeticiones de validación cruzada de {protocol['folds']} folds."
    )
    out.append("")
    out.extend(
        _table(
            ["Modelo", "MAE (min)", "DE del MAE", "R²"],
            [
                [
                    f"**{m['nombre']} (en servicio)**" if m["winner"] else m["nombre"],
                    f"{m['mae']:.2f}",
                    f"{m['mae_std']:.2f}",
                    f"{m['r2']:.3f}",
                ]
                for m in reg["models"]
            ],
        )
    )
    out.append("")
    out.append(
        f"{reg['decision']}. Frente al promedio, el modelo en servicio se equivoca "
        f"{reg['improvement_over_baseline_minutes']:.2f} minutos menos en promedio. "
        f"Se atiende en `POST /predict/cleaning-minutes` (versión `{reg['model_version']}`)."
    )
    out.append("")
    return out


def _class_map(metrics: dict[str, Any]) -> list[str]:
    crit = metrics["criticality"]["comparacion"]
    clean = metrics["cleaning_delay"]["comparacion"]
    svm = next(e for e in clean["models"] if e["id"] == "svm")["hyperparameters"]
    imp = metrics["cleaning_delay"]["imputation"]
    imp_c = metrics["criticality"]["imputation"]
    rows = [
        [
            "SVM: optimizar C y gamma a la vez",
            f"Una sola malla C × gamma dentro del CV (limpieza: C = {svm.get('C')}, gamma = "
            f"{svm.get('gamma')}).",
        ],
        [
            "La mediana no es el método más apropiado; usar KNN",
            f"Imputación KNN dentro de cada fold; ablación contra mediana en ambas tareas "
            f"(limpieza: {imp['chosen']}, criticidad: {imp_c['chosen']}).",
        ],
        [
            "No depender de la imputación interna del modelo",
            "Todos los modelos son pipelines estandarizar -> imputar -> modelo.",
        ],
        [
            "Bagging frente a boosting; LightGBM, XGBoost, CatBoost",
            "Random forest (bagging), gradient boosting, LightGBM y XGBoost en la misma "
            "comparación. CatBoost no se incluyó: su ventaja es manejar variables categóricas "
            "con muchas categorías y aquí hay pocas, de modo que no justifica una dependencia "
            "más pesada en la imagen.",
        ],
        [
            "Importancia de variables para detectar sesgo",
            f"Importancia por permutación por tarea (criticidad: "
            f"«{crit['importance']['by_model'][crit['importance']['shown_model']][0]['label']}»; "
            f"limpieza: "
            f"«{clean['importance']['by_model'][clean['importance']['shown_model']][0]['label']}») "
            f"y desempeño por subgrupo.",
        ],
        ["Stacking", "Modelo «Stacking (LR + RF + GB)» con meta-modelo logístico."],
        [
            "Regresión lineal (variable continua)",
            "Minutos hasta cama lista: línea base, regresión lineal y random forest, con MAE y R².",
        ],
        [
            "Métrica y umbral",
            "F1, sensibilidad y especificidad; umbral elegido fuera de muestra; regla de un "
            "error estándar.",
        ],
    ]
    out = ["## Qué se aplicó de la clase del 06/10", ""]
    out.extend(_table(["Lo que enseñó el profesor", "Dónde aparece"], rows))
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
        "random forest (bagging), gradient boosting de scikit-learn, LightGBM, XGBoost, "
        "stacking (logística + random forest + boosting con meta-modelo logístico) y una red "
        "neuronal pequeña (MLP). Todos reciben los datos estandarizados y con los faltantes "
        "imputados de forma explícita (KNN, ajustado solo con el entrenamiento de cada fold).",
        "",
        "Limitaciones que aplican a todo el reporte: la cohorte de criticidad es una muestra "
        "pequeña (demo de MIMIC-IV-ED); el umbral se elige con las mismas predicciones fuera de "
        "muestra con que se reporta, por lo que la sensibilidad objetivo se cumple por "
        "construcción; los modelos apoyan la priorización y no sustituyen la decisión clínica.",
        "",
    ]
    for name in ("criticality", "cleaning_delay"):
        lines.extend(_task(name, metrics[name]))
    lines.extend(_class_map(metrics))
    return "\n".join(lines).rstrip() + "\n"
