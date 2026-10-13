"""Build the triage cohort: clean a real dataset, cluster it, augment within clusters.

Pure Python on purpose (csv, gzip, math, random, statistics): this runs inside
the API image, which has no numerical library, and 222 rows do not need one.

Pipeline, in the order the course asked for it:

1. Clean MIMIC-IV-ED Demo (``triage`` joined with ``edstays``) with rigid
   clinical ranges. Every discarded row is counted under a named reason.
2. Cluster the real rows with k-means over standardized vital signs and ESI
   acuity, and justify the number of groups (silhouette and inertia).
3. Read each cluster clinically: ranked by admission rate it is far from,
   intermediate or near the critical case.
4. Generate synthetic rows *inside* each cluster, SMOTE-style: interpolate
   between a real row and one of its nearest real neighbours of the same
   cluster. Nothing is drawn from a free distribution.
"""

from __future__ import annotations

import csv
import gzip
import math
import random
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.loinc import (
    DIASTOLIC_BP,
    HEART_RATE,
    OXYGEN_SATURATION,
    PLAUSIBLE_RANGE,
    RESPIRATORY_RATE,
    SYSTOLIC_BP,
)
from app.services.clustering import distance_matrix, kmeans_fit, silhouette, silhouette_samples

SOURCE_PREFIX = "mimic-iv-ed-demo"
SEED = 2026
K_USED = 3
K_SILHOUETTE = range(2, 7)
K_INERTIA = range(1, 7)
NEIGHBORS = 5
MULTIPLIER = 2
MAX_TRIES_PER_ROW = 200

# Clustering features, in vector order. Acuity is the ESI level (1 = most urgent).
FEATURES = ["temperature_c", "heart_rate", "resp_rate", "o2sat", "sbp", "dbp", "acuity"]

# Rigid clinical ranges. Vitals reuse the plausibility limits of the clinical
# model; temperature is tighter than the LOINC one because a triage reading
# below 33 C is a sensor or transcription error, not a patient.
RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (33.0, 42.0),
    "heart_rate": PLAUSIBLE_RANGE[HEART_RATE.code],
    "resp_rate": PLAUSIBLE_RANGE[RESPIRATORY_RATE.code],
    "o2sat": PLAUSIBLE_RANGE[OXYGEN_SATURATION.code],
    "sbp": PLAUSIBLE_RANGE[SYSTOLIC_BP.code],
    "dbp": PLAUSIBLE_RANGE[DIASTOLIC_BP.code],
    "acuity": (1, 5),
}
PAIN_RANGE = (0, 10)

# Column of triage.csv that feeds each feature.
CSV_COLUMN = {
    "temperature_c": "temperature",
    "heart_rate": "heartrate",
    "resp_rate": "resprate",
    "o2sat": "o2sat",
    "sbp": "sbp",
    "dbp": "dbp",
    "acuity": "acuity",
}


# ---------------------------------------------------------------------------
# Parsing and cleaning
# ---------------------------------------------------------------------------


def fahrenheit_to_celsius(value: float) -> float:
    """MIMIC stores temperature in Fahrenheit; the model works in Celsius."""

    return round((value - 32.0) * 5.0 / 9.0, 1)


def parse_float(raw: object) -> float | None:
    """A finite number, or None for empty, non-numeric or NaN input."""

    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_pain(raw: object) -> int | None:
    """Pain score 0..10, or None. The column mixes numbers with free text."""

    value = parse_float(raw)
    if value is None:
        return None
    score = int(round(value))
    return score if PAIN_RANGE[0] <= score <= PAIN_RANGE[1] else None


def read_table(path: str | Path) -> list[dict[str, str]]:
    """Read a CSV, gzipped or not, into a list of dicts."""

    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class CohortRow:
    id: uuid.UUID
    origin: str  # "REAL" or "SYNTHETIC"
    source_ref: str
    temperature_c: float
    heart_rate: int
    resp_rate: int
    o2sat: int
    sbp: int
    dbp: int
    pain: int | None
    acuity: int
    admitted: bool
    cluster: int = 0  # 1-based, assigned after clustering

    def vector(self) -> list[float]:
        return [float(getattr(self, name)) for name in FEATURES]

    def key(self) -> tuple[float, ...]:
        return tuple(self.vector())


def clean_rows(
    triage: Iterable[dict[str, str]],
    stays: Iterable[dict[str, str]],
    rng: random.Random,
) -> tuple[list[CohortRow], Counter[str]]:
    """Join, convert and filter. Returns the kept rows and the drop reasons."""

    disposition: dict[tuple[str, str], str] = {}
    for stay in stays:
        key = (str(stay.get("subject_id", "")).strip(), str(stay.get("stay_id", "")).strip())
        disposition[key] = str(stay.get("disposition", "")).strip().upper()

    kept: list[CohortRow] = []
    drops: Counter[str] = Counter()
    seen: set[str] = set()

    for row in triage:
        subject = str(row.get("subject_id", "")).strip()
        stay_id = str(row.get("stay_id", "")).strip()
        if (subject, stay_id) not in disposition:
            drops["no_matching_stay"] += 1
            continue
        if stay_id in seen:
            drops["duplicate_stay"] += 1
            continue

        values: dict[str, float] = {}
        missing = None
        for name in FEATURES:
            number = parse_float(row.get(CSV_COLUMN[name]))
            if number is None:
                missing = name
                break
            values[name] = number
        if missing:
            drops[f"missing_{missing}"] += 1
            continue

        values["temperature_c"] = fahrenheit_to_celsius(values["temperature_c"])
        out_of_range = next(
            (
                name
                for name in FEATURES
                if not RANGES[name][0] <= values[name] <= RANGES[name][1]
            ),
            None,
        )
        if out_of_range:
            drops[f"out_of_range_{out_of_range}"] += 1
            continue
        if values["acuity"] != int(values["acuity"]):
            drops["non_integer_acuity"] += 1
            continue
        sbp, dbp = int(round(values["sbp"])), int(round(values["dbp"]))
        if sbp <= dbp:
            drops["sbp_not_above_dbp"] += 1
            continue

        seen.add(stay_id)
        kept.append(
            CohortRow(
                id=uuid.UUID(int=rng.getrandbits(128), version=4),
                origin="REAL",
                source_ref=f"{SOURCE_PREFIX}:stay_id={stay_id}",
                temperature_c=values["temperature_c"],
                heart_rate=int(round(values["heart_rate"])),
                resp_rate=int(round(values["resp_rate"])),
                o2sat=int(round(values["o2sat"])),
                sbp=sbp,
                dbp=dbp,
                pain=parse_pain(row.get("pain")),
                acuity=int(values["acuity"]),
                admitted=disposition[(subject, stay_id)] == "ADMITTED",
            )
        )
    return kept, drops


# ---------------------------------------------------------------------------
# Standardization and clustering
# ---------------------------------------------------------------------------


@dataclass
class Scaler:
    means: list[float]
    stds: list[float]

    @classmethod
    def fit(cls, rows: list[CohortRow]) -> Scaler:
        columns = list(zip(*(r.vector() for r in rows)))
        means = [sum(c) / len(c) for c in columns]
        # Constant column: divide by 1 so it contributes nothing instead of NaN.
        stds = [statistics.pstdev(c) or 1.0 for c in columns]
        return cls(means, stds)

    def transform(self, vector: list[float]) -> list[float]:
        return [(x - m) / s for x, m, s in zip(vector, self.means, self.stds)]


def evaluate_k(points: list[list[float]], seed: int = SEED) -> dict[str, Any]:
    """Silhouette for k=2..6 and inertia for k=1..6 (the elbow curve)."""

    fits = {k: kmeans_fit(points, k, seed) for k in K_INERTIA}
    distances = distance_matrix(points)
    silhouettes = {k: round(silhouette(distances, fits[k][0]), 4) for k in K_SILHOUETTE}
    return {
        "fits": fits,
        "distances": distances,
        "silhouette_by_k": silhouettes,
        "inertia_by_k": {k: round(fits[k][1], 3) for k in K_INERTIA},
        "best_k_by_silhouette": max(silhouettes, key=silhouettes.get),
    }


def _zone(rank: int, k: int) -> str:
    if rank == 1:
        return "FAR"
    if rank == k:
        return "NEAR"
    return "INTERMEDIATE"


def _decimal_es(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def describe_cluster(zone: str, centroid: dict[str, float], rate: float) -> str:
    """Clinical reading of a cluster, in Spanish, from its centroid and admission rate."""

    percent = round(rate * 100)
    title = {
        "FAR": "Lejos del caso critico",
        "INTERMEDIATE": "Zona intermedia entre el caso estable y el critico",
        "NEAR": "Cerca del caso critico",
    }[zone]
    text = (
        f"{title}: SpO2 media {centroid['o2sat']:.0f} %, FC {centroid['heart_rate']:.0f}, "
        f"FR {centroid['resp_rate']:.0f}, PAS {centroid['sbp']:.0f}, "
        f"ESI medio {_decimal_es(centroid['acuity'])}; "
        f"{percent} % terminaron hospitalizados"
    )
    if zone == "INTERMEDIATE":
        if 0.35 <= rate <= 0.65:
            split = (
                "la mitad ingresa y la mitad no"
                if abs(rate - 0.5) <= 0.05
                else f"{percent} % ingresa y {100 - percent} % no"
            )
            text += f"; zona de confusion: {split}; aqui el modelo se equivoca mas"
        else:
            text += (
                "; zona de transicion: sus signos se parecen a los de los grupos vecinos "
                "y el modelo puede confundirse cerca de esas fronteras"
            )
    return text


@dataclass
class ClusterSummary:
    index: int
    zone: str
    description: str
    admission_rate: float
    centroid: dict[str, float]
    silhouette: float | None
    n_real: int = 0
    n_synthetic: int = 0


def assign_clusters(
    rows: list[CohortRow], labels: list[int], sample_scores: list[float]
) -> list[ClusterSummary]:
    """Rank raw k-means labels by admission rate and describe each cluster.

    Rank 1 is the lowest admission rate (far from the critical case). Ties are
    broken by mean ESI: a higher level number is less urgent, so it goes first.
    """

    raw = sorted(set(labels))
    members = {g: [i for i, label in enumerate(labels) if label == g] for g in raw}

    def stats(g: int) -> tuple[float, float]:
        rs = [rows[i] for i in members[g]]
        return (
            sum(r.admitted for r in rs) / len(rs),
            -sum(r.acuity for r in rs) / len(rs),
        )

    ordered = sorted(raw, key=stats)
    rank_of = {g: rank for rank, g in enumerate(ordered, start=1)}
    for row, label in zip(rows, labels):
        row.cluster = rank_of[label]

    summaries = []
    for g in ordered:
        rs = [rows[i] for i in members[g]]
        rate = sum(r.admitted for r in rs) / len(rs)
        centroid = {
            name: round(sum(getattr(r, name) for r in rs) / len(rs), 2) for name in FEATURES
        }
        zone = _zone(rank_of[g], len(raw))
        scores = [sample_scores[i] for i in members[g]]
        summaries.append(
            ClusterSummary(
                index=rank_of[g],
                zone=zone,
                description=describe_cluster(zone, centroid, rate),
                admission_rate=round(rate, 4),
                centroid=centroid,
                silhouette=round(sum(scores) / len(scores), 4),
                n_real=len(rs),
            )
        )
    return summaries


# ---------------------------------------------------------------------------
# Augmentation within clusters
# ---------------------------------------------------------------------------


def _clip(value: float, name: str) -> float:
    low, high = RANGES[name]
    return min(max(value, low), high)


def _interpolate(base: CohortRow, neighbour: CohortRow, u: float) -> dict[str, Any]:
    """base + u * (neighbour - base), at clinical precision and inside the rigid ranges."""

    values: dict[str, Any] = {}
    for name in FEATURES:
        raw = getattr(base, name) + u * (getattr(neighbour, name) - getattr(base, name))
        raw = _clip(raw, name)
        values[name] = round(raw, 1) if name == "temperature_c" else int(round(raw))
    if values["sbp"] <= values["dbp"]:
        # Interpolating two valid pairs cannot cross in theory, but rounding and
        # clipping can; fall back to the base row's pair instead of inventing one.
        values["sbp"], values["dbp"] = base.sbp, base.dbp
    if base.pain is not None and neighbour.pain is not None:
        pain = base.pain + u * (neighbour.pain - base.pain)
        values["pain"] = int(min(max(round(pain), PAIN_RANGE[0]), PAIN_RANGE[1]))
    else:
        values["pain"] = None
    return values


def augment_within_clusters(
    rows: list[CohortRow],
    scaler: Scaler,
    rng: random.Random,
    multiplier: int = MULTIPLIER,
    neighbours: int = NEIGHBORS,
) -> tuple[list[CohortRow], dict[str, Any]]:
    """SMOTE-style synthetic rows, each tied to a real base row and neighbour of one cluster.

    A candidate identical to a real (or already generated) row is discarded and
    resampled: a synthetic row that copies a real patient is not new data and
    would defeat the privacy point of generating it.
    """

    scaled = {r.id: scaler.transform(r.vector()) for r in rows}
    known = {r.key() for r in rows}
    by_cluster: dict[int, list[CohortRow]] = {}
    for row in rows:
        by_cluster.setdefault(row.cluster, []).append(row)

    synthetic: list[CohortRow] = []
    resampled = 0
    short: dict[int, int] = {}

    for cluster, members in sorted(by_cluster.items()):
        if len(members) < 2:
            short[cluster] = multiplier * len(members)
            continue
        nearest: dict[uuid.UUID, list[CohortRow]] = {}
        for row in members:
            others = sorted(
                (o for o in members if o.id != row.id),
                key=lambda o: math.dist(scaled[row.id], scaled[o.id]),
            )
            nearest[row.id] = others[:neighbours]

        target = multiplier * len(members)
        made, tries = 0, 0
        while made < target and tries < target * MAX_TRIES_PER_ROW:
            tries += 1
            base = rng.choice(members)
            neighbour = rng.choice(nearest[base.id])
            u = round(rng.random(), 2)
            if u <= 0.0 or u >= 1.0:
                resampled += 1
                continue
            values = _interpolate(base, neighbour, u)
            candidate = tuple(float(values[name]) for name in FEATURES)
            if candidate in known:
                resampled += 1
                continue
            known.add(candidate)
            synthetic.append(
                CohortRow(
                    id=uuid.UUID(int=rng.getrandbits(128), version=4),
                    origin="SYNTHETIC",
                    source_ref=f"base={base.id};vecino={neighbour.id};u={u:.2f}",
                    admitted=base.admitted if u < 0.5 else neighbour.admitted,
                    cluster=base.cluster,
                    **values,
                )
            )
            made += 1
        if made < target:
            short[cluster] = target - made

    # Privacy control: how close is each synthetic row to its nearest real row.
    real_points = [scaled[r.id] for r in rows]
    distances = [
        min(math.dist(scaler.transform(s.vector()), p) for p in real_points) for s in synthetic
    ]
    report = {
        "resampled_candidates": resampled,
        "clusters_short_of_target": short,
        "nearest_real_distance_min": round(min(distances), 4) if distances else None,
        "nearest_real_distance_median": round(statistics.median(distances), 4)
        if distances
        else None,
        "nearest_real_distance_zero": sum(1 for d in distances if d == 0),
    }
    return synthetic, report


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class CohortResult:
    rows: list[CohortRow]
    clusters: list[ClusterSummary]
    summary: dict[str, Any] = field(default_factory=dict)


def build_cohort(
    triage: Iterable[dict[str, str]],
    stays: Iterable[dict[str, str]],
    *,
    k: int = K_USED,
    multiplier: int = MULTIPLIER,
    seed: int = SEED,
) -> CohortResult:
    triage = list(triage)
    rng = random.Random(seed)
    real, drops = clean_rows(triage, stays, rng)
    if len(real) < max(10, 3 * max(K_SILHOUETTE)):
        raise ValueError(f"Cohorte demasiado pequena tras la limpieza: {len(real)} filas")

    scaler = Scaler.fit(real)
    points = [scaler.transform(r.vector()) for r in real]
    evaluation = evaluate_k(points, seed)
    labels = evaluation["fits"][k][0]
    scores = silhouette_samples(evaluation["distances"], labels)
    clusters = assign_clusters(real, labels, scores)

    synthetic, privacy = augment_within_clusters(real, scaler, rng, multiplier)
    for cluster in clusters:
        cluster.n_synthetic = sum(1 for s in synthetic if s.cluster == cluster.index)

    def rate(rs: list[CohortRow]) -> float | None:
        return round(sum(r.admitted for r in rs) / len(rs), 4) if rs else None

    summary = {
        "source": SOURCE_PREFIX,
        "seed": seed,
        "k_used": k,
        "k_justification": (
            "k=3 corresponde a las tres zonas clinicas pedidas (lejos, intermedio y cerca "
            "del caso critico); se reportan silueta e inercia de todos los k para contrastarlo."
        ),
        "rows_triage": len(triage),
        "rows_kept": len(real),
        "dropped_total": sum(drops.values()),
        "drop_reasons": dict(sorted(drops.items())),
        "admission_rate_real": rate(real),
        "silhouette_by_k": evaluation["silhouette_by_k"],
        "inertia_by_k": evaluation["inertia_by_k"],
        "best_k_by_silhouette": evaluation["best_k_by_silhouette"],
        "silhouette_k_used": evaluation["silhouette_by_k"].get(k),
        "multiplier": multiplier,
        "clusters": [
            {
                "cluster": c.index,
                "zone": c.zone,
                "n_real": c.n_real,
                "n_synthetic": c.n_synthetic,
                "admission_rate_real": c.admission_rate,
                "admission_rate_synthetic": rate(
                    [s for s in synthetic if s.cluster == c.index]
                ),
                "silhouette": c.silhouette,
                "centroid": c.centroid,
                "description": c.description,
            }
            for c in clusters
        ],
        "privacy": privacy,
    }
    return CohortResult(rows=real + synthetic, clusters=clusters, summary=summary)
