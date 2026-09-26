"""Automatic grouping of patients from their vital signs.

The clinical profile stored on each patient is the label the data generator
used. This module never looks at it while grouping: it builds one vector per
patient from measurements alone, lets k-means find the groups, and only then
compares them with the stored profiles. That comparison is what says whether
the vital signs actually carry the structure -- if the algorithm could see the
label, agreeing with it would prove nothing.

The number of groups is a decision too. It is taken with the silhouette
coefficient (how much closer each patient is to its own group than to the
nearest other one), not from the fact that the generator happens to use six
profiles: a real network would not know that number in advance.

Plain Python on purpose. Four hundred patients by six variables is a few
thousand distance computations per iteration; a numerical library would add a
heavy dependency to the API image for no measurable gain.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinical import Observation
from app.models.patient import Patient

# One entry per feature: (name, LOINC code or None when derived, unit).
FEATURES: list[tuple[str, str | None, str]] = [
    ("sistolica", "8480-6", "mm[Hg]"),
    ("glucosa", "2339-0", "mg/dL"),
    ("saturacion", "59408-5", "%"),
    ("frecuencia_cardiaca", "8867-4", "/min"),
    ("imc", None, "kg/m2"),
    ("edad", None, "a"),
]
WEIGHT = "29463-7"
HEIGHT = "8302-2"

SEED = 20260906
K_RANGE = range(2, 9)
RESTARTS = 8
MAX_ITERATIONS = 100


@dataclass
class PatientVector:
    patient_id: str
    profile: str | None
    raw: list[float]
    scaled: list[float]


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def _age(birth: date, today: date) -> float:
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return float(years)


def load_vectors(db: Session) -> list[PatientVector]:
    """One vector per patient: the mean of each measurement across visits.

    A patient missing any feature is left out rather than filled in: an
    invented value would pull that patient toward whatever the fill value
    represents and blur exactly the boundaries being measured.
    """

    codes = [code for _, code, _ in FEATURES if code] + [WEIGHT, HEIGHT]
    rows = db.execute(
        select(Observation.patient_id, Observation.code, func.avg(Observation.value_numeric))
        .where(
            Observation.deleted_at.is_(None),
            Observation.value_numeric.is_not(None),
            Observation.code.in_(codes),
        )
        .group_by(Observation.patient_id, Observation.code)
    ).all()

    measured: dict[object, dict[str, float]] = {}
    for patient_id, code, value in rows:
        measured.setdefault(patient_id, {})[code] = float(value)

    patients = {
        p.id: p
        for p in db.scalars(
            select(Patient).where(
                Patient.deleted_at.is_(None), Patient.id.in_(list(measured))
            )
        )
    }

    today = date.today()
    vectors: list[PatientVector] = []
    for patient_id, values in measured.items():
        patient = patients.get(patient_id)
        if patient is None or WEIGHT not in values or HEIGHT not in values:
            continue
        if any(code and code not in values for _, code, _ in FEATURES):
            continue
        bmi = values[WEIGHT] / (values[HEIGHT] / 100) ** 2
        raw = []
        for name, code, _ in FEATURES:
            if code:
                raw.append(values[code])
            elif name == "imc":
                raw.append(bmi)
            else:
                raw.append(_age(patient.birth_date, today))
        profile = patient.clinical_profile.value if patient.clinical_profile else None
        vectors.append(PatientVector(str(patient_id), profile, raw, []))

    # z-score: without it glucose (tens to hundreds) would dominate saturation
    # (a few points) and the grouping would be a grouping by glucose alone.
    columns = list(zip(*(v.raw for v in vectors))) if vectors else []
    means = [sum(c) / len(c) for c in columns]
    stdevs = [
        math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) or 1.0
        for c, m in zip(columns, means)
    ]
    for v in vectors:
        v.scaled = [(x - m) / s for x, m, s in zip(v.raw, means, stdevs)]
    return vectors


# ---------------------------------------------------------------------------
# k-means
# ---------------------------------------------------------------------------


def _distance2(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _kmeans_once(points: list[list[float]], k: int, rng: random.Random) -> tuple[list[int], float]:
    # k-means++: each new centre is drawn with probability proportional to its
    # squared distance to the nearest existing one, which avoids starting with
    # two centres inside the same group.
    centres = [points[rng.randrange(len(points))]]
    while len(centres) < k:
        weights = [min(_distance2(p, c) for c in centres) for p in points]
        total = sum(weights)
        if total == 0:
            centres.append(points[rng.randrange(len(points))])
            continue
        target = rng.random() * total
        running = 0.0
        for point, weight in zip(points, weights):
            running += weight
            if running >= target:
                centres.append(point)
                break

    labels: list[int] = []
    for _ in range(MAX_ITERATIONS):
        new_labels = [
            min(range(k), key=lambda j: _distance2(p, centres[j])) for p in points
        ]
        if new_labels == labels:
            break
        labels = new_labels
        for j in range(k):
            members = [p for p, label in zip(points, labels) if label == j]
            if members:
                centres[j] = [sum(col) / len(members) for col in zip(*members)]

    inertia = sum(_distance2(p, centres[label]) for p, label in zip(points, labels))
    return labels, inertia


def kmeans(points: list[list[float]], k: int) -> list[int]:
    """Best of several restarts, with a fixed seed so the answer is stable."""

    rng = random.Random(SEED + k)
    best_labels, best_inertia = [], math.inf
    for _ in range(RESTARTS):
        labels, inertia = _kmeans_once(points, k, rng)
        if inertia < best_inertia:
            best_labels, best_inertia = labels, inertia
    return best_labels


def distance_matrix(points: list[list[float]]) -> list[list[float]]:
    """Pairwise distances, computed once and shared by every candidate k."""

    return [[math.sqrt(_distance2(a, b)) for b in points] for a in points]


def silhouette(distances: list[list[float]], labels: list[int]) -> float:
    """Mean silhouette: +1 well separated, 0 on the border, negative misplaced."""

    sizes = Counter(labels)
    scores = []
    for i, own in enumerate(labels):
        if sizes[own] < 2:
            scores.append(0.0)
            continue
        # One pass over the row, adding each distance to its group's total.
        totals: dict[int, float] = {}
        for label, d in zip(labels, distances[i]):
            totals[label] = totals.get(label, 0.0) + d
        a = totals[own] / (sizes[own] - 1)
        b = min(totals[g] / sizes[g] for g in sizes if g != own)
        scores.append((b - a) / max(a, b) if max(a, b) else 0.0)
    return sum(scores) / len(scores)


def adjusted_rand_index(truth: list[str], found: list[int]) -> float:
    """Agreement between two groupings, corrected for chance.

    0 is what random labels would score, 1 is identical groupings. Unlike plain
    accuracy it does not need the group numbers to match the profile names.
    """

    def pairs(n: int) -> float:
        return n * (n - 1) / 2

    table = Counter(zip(truth, found))
    sum_cells = sum(pairs(n) for n in table.values())
    sum_truth = sum(pairs(n) for n in Counter(truth).values())
    sum_found = sum(pairs(n) for n in Counter(found).values())
    total = pairs(len(truth))
    expected = sum_truth * sum_found / total if total else 0.0
    maximum = (sum_truth + sum_found) / 2
    return (sum_cells - expected) / (maximum - expected) if maximum != expected else 1.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def patient_clusters(db: Session, k: int | None = None) -> dict[str, object]:
    """Group patients, justify the number of groups, and grade the result."""

    vectors = load_vectors(db)
    if len(vectors) < 2 * max(K_RANGE):
        return {"patients": len(vectors), "message": "No hay suficientes pacientes con mediciones completas para agrupar."}

    points = [v.scaled for v in vectors]
    distances = distance_matrix(points)
    candidates = []
    for candidate in K_RANGE:
        labels = kmeans(points, candidate)
        candidates.append(
            {"k": candidate, "silhouette": round(silhouette(distances, labels), 3), "labels": labels}
        )
    best = max(candidates, key=lambda c: c["silhouette"])
    chosen = next((c for c in candidates if c["k"] == k), best) if k else best

    labels = chosen["labels"]
    labelled = [(v, label) for v, label in zip(vectors, labels) if v.profile]
    ari = adjusted_rand_index([v.profile for v, _ in labelled], [label for _, label in labelled]) if labelled else None

    clusters = []
    for group in sorted(set(labels)):
        members = [v for v, label in zip(vectors, labels) if label == group]
        profiles = Counter(v.profile for v in members if v.profile)
        dominant, dominant_count = profiles.most_common(1)[0] if profiles else (None, 0)
        centre = [sum(col) / len(members) for col in zip(*(v.raw for v in members))]
        clusters.append(
            {
                "cluster": group + 1,
                "patients": len(members),
                "centroid": {name: round(value, 1) for (name, _, _), value in zip(FEATURES, centre)},
                "dominant_profile": dominant,
                "dominant_share": round(dominant_count / len(members), 3) if members else 0.0,
                "profiles": dict(profiles.most_common()),
            }
        )
    clusters.sort(key=lambda c: -c["patients"])
    purity = sum(c["dominant_share"] * c["patients"] for c in clusters) / len(vectors)

    return {
        "patients": len(vectors),
        "features": [{"name": name, "loinc": code, "unit": unit} for name, code, unit in FEATURES],
        "k": chosen["k"],
        "k_chosen_by": "silhouette" if not k else "requested",
        "silhouette_by_k": [{"k": c["k"], "silhouette": c["silhouette"]} for c in candidates],
        "silhouette": chosen["silhouette"],
        "adjusted_rand_index": round(ari, 3) if ari is not None else None,
        "purity": round(purity, 3),
        "clusters": clusters,
    }
