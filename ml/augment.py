"""Within-cluster augmentation (SMOTE-like) for the criticality training set.

Same method as the triage cohort of the API (``app/services/cohort_builder.py``):
k-means groups the real rows, and each synthetic row interpolates a real row with
one of its nearest real neighbours of the *same* group. Two rules keep it honest:

* It only ever runs on the training part of a split. Evaluation rows are real.
* A synthetic row never duplicates a real one.

The difference with the cohort table is the clustering space: here it uses age
and vital signs (no ESI acuity, which the API cannot supply at prediction time).
"""

from __future__ import annotations

import numpy as np
from sklearn.cluster import KMeans

from features import CRITICALITY_RANGES, FeatureSpec

K_CLUSTERS = 3
NEIGHBOURS = 5
MULTIPLIER = 2
MAX_TRIES = 200
ROUNDING = {"temperature_c": 1}  # decimals; everything else is an integer


def augment(
    X: np.ndarray,
    y: np.ndarray,
    spec: FeatureSpec,
    *,
    seed: int,
    multiplier: int = MULTIPLIER,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ONLY the synthetic rows (features, labels) built from ``X``/``y``."""

    names = spec.names
    numeric = [names.index(n) for n in spec.numeric]
    rng = np.random.default_rng(seed)
    if len(X) < K_CLUSTERS * 2:
        return np.empty((0, X.shape[1])), np.empty(0, dtype=int)

    mean = X[:, numeric].mean(axis=0)
    std = X[:, numeric].std(axis=0)
    std[std == 0] = 1.0
    scaled = (X[:, numeric] - mean) / std
    labels = KMeans(n_clusters=K_CLUSTERS, n_init=8, random_state=seed).fit_predict(scaled)

    seen = {tuple(row) for row in X}
    sbp_i, dbp_i = names.index("sbp"), names.index("dbp")
    new_rows: list[np.ndarray] = []
    new_y: list[int] = []

    for cluster in range(K_CLUSTERS):
        members = np.flatnonzero(labels == cluster)
        if len(members) < 2:
            continue
        distances = np.linalg.norm(scaled[members][:, None] - scaled[members][None], axis=2)
        np.fill_diagonal(distances, np.inf)
        k = min(NEIGHBOURS, len(members) - 1)
        nearest = np.argsort(distances, axis=1)[:, :k]

        for _ in range(multiplier * len(members)):
            for _attempt in range(MAX_TRIES):
                b = int(rng.integers(len(members)))
                n = int(nearest[b][rng.integers(k)])
                base, other = X[members[b]], X[members[n]]
                u = float(rng.random())
                row = base + u * (other - base)
                # One-hot blocks do not interpolate: take the base's or the neighbour's.
                source = base if u < 0.5 else other
                for i, name in enumerate(names):
                    if i not in numeric:
                        row[i] = source[i]
                for name in spec.numeric:
                    i = names.index(name)
                    low, high = CRITICALITY_RANGES[name]
                    row[i] = np.clip(np.round(row[i], ROUNDING.get(name, 0)), low, high)
                if row[sbp_i] <= row[dbp_i]:
                    row[sbp_i], row[dbp_i] = base[sbp_i], base[dbp_i]
                key = tuple(row)
                if key in seen:
                    continue
                seen.add(key)
                new_rows.append(row)
                new_y.append(int(y[members[b]] if u < 0.5 else y[members[n]]))
                break

    if not new_rows:
        return np.empty((0, X.shape[1])), np.empty(0, dtype=int)
    return np.vstack(new_rows), np.array(new_y, dtype=int)
