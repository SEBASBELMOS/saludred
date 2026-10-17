"""Feature specification shared by training and serving.

Both ends encode a request in exactly the same way because they use the same
object: numeric features, categorical features with a fixed list of categories,
and the defaults (training medians and modes) that fill whatever is missing.
Unknown categories become an all-zero block instead of an error, and missing
values are imputed and reported back, never silently guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Plausible bounds. Training clips synthetic rows to them and the service
# rejects requests outside of them: an out-of-range value is a typing error,
# not a very sick patient.
CRITICALITY_RANGES: dict[str, tuple[float, float]] = {
    "age": (0, 120),
    "temperature_c": (33.0, 42.0),
    "heart_rate": (30, 220),
    "resp_rate": (6, 60),
    "o2sat": (50, 100),
    "sbp": (50, 260),
    "dbp": (30, 150),
    "acuity": (1, 5),
}


@dataclass
class FeatureSpec:
    numeric: list[str]
    categorical: dict[str, list[str]] = field(default_factory=dict)
    # Filled by ``fit_defaults``: most frequent value for categories (used when one is
    # missing) and medians for numerics (reference only: the pipeline imputes them).
    defaults: dict[str, float | str] = field(default_factory=dict)

    @property
    def inputs(self) -> list[str]:
        return [*self.numeric, *self.categorical]

    @property
    def names(self) -> list[str]:
        """Names of the encoded columns, in order."""

        columns = list(self.numeric)
        for feature, categories in self.categorical.items():
            columns.extend(f"{feature}={category}" for category in categories)
        return columns

    def fit_defaults(self, records: list[dict[str, object]]) -> None:
        for name in self.numeric:
            values = [float(r[name]) for r in records if r.get(name) is not None]
            self.defaults[name] = float(np.median(values))
        for name, categories in self.categorical.items():
            counts = {c: sum(1 for r in records if r.get(name) == c) for c in categories}
            self.defaults[name] = max(counts, key=lambda c: counts[c])

    def encode(self, record: dict[str, object]) -> tuple[np.ndarray, list[str]]:
        """Return the encoded vector and the inputs that had to be imputed.

        A missing NUMERIC input stays NaN here: the fitted imputer inside the model
        pipeline (KNN by default) fills it, so the same step runs in training folds
        and in serving. A missing CATEGORICAL input takes the training mode.
        """

        imputed: list[str] = []
        vector: list[float] = []
        for name in self.numeric:
            value = record.get(name)
            if value is None:
                vector.append(np.nan)
                imputed.append(name)
            else:
                vector.append(float(value))
        for name, categories in self.categorical.items():
            value = record.get(name)
            if value is None:
                value = self.defaults[name]
                imputed.append(name)
            vector.extend(1.0 if value == category else 0.0 for category in categories)
        return np.array(vector, dtype=float), imputed

    def matrix(self, records: list[dict[str, object]]) -> np.ndarray:
        return np.vstack([self.encode(r)[0] for r in records])


CRITICALITY_SPEC = FeatureSpec(
    numeric=["age", "temperature_c", "heart_rate", "resp_rate", "o2sat", "sbp", "dbp", "acuity"],
    categorical={"sex": ["female", "male"]},
)

CLEANING_SERVICES = ["Hospitalizacion", "UCI Adultos", "Urgencias"]
CLEANING_SPEC = FeatureSpec(
    numeric=[
        "release_hour",
        "weekday",
        "previous_stay_hours",
        "cleaning_staff_on_shift",
        "pending_cleaning_beds",
        "service_occupancy",
        "curtains_change_required",
        "patient_waiting",
    ],
    categorical={
        "service": CLEANING_SERVICES,
        "release_reason": ["discharge", "transfer", "isolation_end"],
        "isolation": ["none", "contact", "droplet", "spores"],
        "shift": ["morning", "afternoon", "night"],
    },
)


def shift_of(hour: int | float | None) -> str | None:
    """Work shift of a release hour (06-14 morning, 14-22 afternoon, else night)."""

    if hour is None:
        return None
    hour = int(hour)
    if 6 <= hour < 14:
        return "morning"
    if 14 <= hour < 22:
        return "afternoon"
    return "night"
