"""Global explanations: permutation importance tables and odds ratios.

Both are read from the same training run as the comparison. Labels and
one-line readings are Spanish because they are shown to clinicians as they are.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.pipeline import Pipeline

from features import FeatureSpec

INPUT_LABELS = {
    "age": "Edad",
    "sex": "Sexo",
    "temperature_c": "Temperatura",
    "heart_rate": "Frecuencia cardíaca",
    "resp_rate": "Frecuencia respiratoria",
    "o2sat": "Saturación de oxígeno",
    "sbp": "Presión sistólica",
    "dbp": "Presión diastólica",
    "acuity": "Nivel de triage ESI",
    "release_hour": "Hora de liberación",
    "weekday": "Día de la semana",
    "previous_stay_hours": "Horas de estancia previa",
    "cleaning_staff_on_shift": "Personal de limpieza en turno",
    "pending_cleaning_beds": "Camas pendientes de limpieza",
    "service_occupancy": "Ocupación del servicio",
    "curtains_change_required": "Cambio de cortinas",
    "patient_waiting": "Paciente esperando la cama",
    "service": "Servicio",
    "release_reason": "Motivo de liberación",
    "isolation": "Aislamiento",
    "shift": "Turno",
}
CATEGORY_LABELS = {
    "female": "mujer",
    "male": "hombre",
    "discharge": "alta",
    "transfer": "traslado",
    "isolation_end": "fin de aislamiento",
    "none": "ninguno",
    "contact": "contacto",
    "droplet": "gotas",
    "spores": "esporas",
    "morning": "mañana",
    "afternoon": "tarde",
    "night": "noche",
}


def label_of(name: str) -> str:
    """Spanish label of an input (``age``) or of an encoded column (``sex=male``)."""

    if "=" in name:
        base, value = name.split("=", 1)
        return f"{INPUT_LABELS.get(base, base)}: {CATEGORY_LABELS.get(value, value)}"
    return INPUT_LABELS.get(name, name)


def importance_items(drops: dict[str, float]) -> list[dict[str, Any]]:
    """Inputs sorted by how much the held-out AUC falls when they are shuffled."""

    items = [
        {"feature": name, "label": label_of(name), "importance": round(drop, 3)}
        for name, drop in drops.items()
    ]
    items.sort(key=lambda item: -float(item["importance"]))
    return items


def importance_reading(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    top = items[0]
    return (
        f"La variable que más pesa es «{top['label']}»: al mezclar sus valores entre "
        f"pacientes el AUC baja {float(top['importance']):.3f}. Cuanto más baja el AUC, "
        f"más depende el modelo de esa variable."
    )


def odds_ratios(
    pipeline: Pipeline, spec: FeatureSpec, positive: str
) -> list[dict[str, Any]]:
    """Odds ratios of a standardized logistic regression.

    A numeric input: exp(coefficient), the change per one standard deviation. A
    category (one-hot column): exp(coefficient / scale), the change of being in it
    against not being in it.
    """

    coefficients = pipeline.named_steps["model"].coef_[0]
    scales = pipeline.named_steps["scale"].scale_
    items: list[dict[str, Any]] = []
    for name, coefficient, scale in zip(spec.names, coefficients, scales):
        is_category = "=" in name
        ratio = float(np.exp(coefficient / (scale if is_category and scale else 1.0)))
        change = abs(ratio - 1) * 100
        direction = "aumentan" if ratio >= 1 else "disminuyen"
        subject = (
            f"Estar en «{label_of(name)}» frente a no estarlo"
            if is_category
            else f"Por cada desviación estándar más de «{label_of(name)}»"
        )
        items.append(
            {
                "feature": name,
                "label": label_of(name),
                "coefficient": round(float(coefficient), 3),
                "odds_ratio": round(ratio, 2),
                "unit": "categoría" if is_category else "desviación estándar",
                "reading": (
                    f"{subject}: los momios de {positive} {direction} {change:.0f}% "
                    f"(razón de momios {ratio:.2f})."
                ),
            }
        )
    items.sort(key=lambda item: -abs(float(item["coefficient"])))
    return items
