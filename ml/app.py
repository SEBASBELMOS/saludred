"""Prediction service for SaludRed.

This service never touches the database and never receives identifiers: the
API sends already-minimized clinical variables and an opaque pseudonym. Every
request model forbids unknown fields, so a name, document, e-mail or phone
number is refused with a 422 instead of being silently ignored.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import joblib
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from features import CRITICALITY_RANGES, shift_of

DEFAULT_ARTIFACTS = Path(os.environ.get("ARTIFACT_DIR", Path(__file__).resolve().parent / "artifacts"))
TOP_FEATURES = 3
MIN_CRITICALITY_INPUTS = 3


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _bounded(name: str) -> Any:
    low, high = CRITICALITY_RANGES[name]
    return Field(default=None, ge=low, le=high)


class CriticalityRequest(Strict):
    pseudonym: str = Field(pattern=r"^pz-[0-9a-f]{16}$")
    age: int | None = _bounded("age")
    sex: Literal["male", "female"] | None = None
    temperature_c: float | None = _bounded("temperature_c")
    heart_rate: float | None = _bounded("heart_rate")
    resp_rate: float | None = _bounded("resp_rate")
    o2sat: float | None = _bounded("o2sat")
    sbp: float | None = _bounded("sbp")
    dbp: float | None = _bounded("dbp")
    acuity: int | None = _bounded("acuity")

    @model_validator(mode="after")
    def _enough_and_coherent(self) -> CriticalityRequest:
        given = [
            name
            for name in (*CRITICALITY_RANGES, "sex")
            if getattr(self, name) is not None
        ]
        if len(given) < MIN_CRITICALITY_INPUTS:
            raise ValueError(
                f"At least {MIN_CRITICALITY_INPUTS} clinical variables are required"
            )
        if self.sbp is not None and self.dbp is not None and self.sbp <= self.dbp:
            raise ValueError("sbp must be greater than dbp")
        return self


class CleaningRequest(Strict):
    service: str | None = Field(default=None, max_length=80)
    release_reason: Literal["discharge", "transfer", "isolation_end"] | None = None
    isolation: Literal["none", "contact", "droplet", "spores"] | None = None
    release_hour: int | None = Field(default=None, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=1, le=7)
    previous_stay_hours: float | None = Field(default=None, ge=0, le=2000)
    cleaning_staff_on_shift: int | None = Field(default=None, ge=0, le=100)
    pending_cleaning_beds: int | None = Field(default=None, ge=0, le=500)
    service_occupancy: float | None = Field(default=None, ge=0, le=1)
    curtains_change_required: bool | None = None
    patient_waiting: bool | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class Contribution(BaseModel):
    feature: str
    value: float | str
    contribution: float
    imputed: bool


class Prediction(BaseModel):
    probability: float
    nivel: str
    positive: bool
    threshold: float
    model_version: str
    model_family: str
    explanation: str
    imputed: list[str]
    unrecognized: list[str]
    top_features: list[Contribution]
    pseudonym: str | None = None


# ---------------------------------------------------------------------------
# Model handling
# ---------------------------------------------------------------------------

LINEAR_EXPLANATION = "coeficiente x valor estandarizado (log-odds); el modelo es lineal"
OCCLUSION_EXPLANATION = (
    "cambio de probabilidad de este paciente al reemplazar la variable por un valor imputado "
    "con pacientes parecidos del entrenamiento (explicación local, válida para cualquier modelo)"
)


class Bundle:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.name: str = raw["name"]
        self.version: str = raw["version"]
        self.family: str = raw["family"]
        self.explain: str = raw["explain"]
        self.spec = raw["spec"]
        self.estimator = raw["estimator"]
        self.threshold: float = raw["threshold"]
        self.positive_label: str = raw["positive_label"]
        self.negative_label: str = raw["negative_label"]

    def _probability(self, matrix: np.ndarray) -> np.ndarray:
        return self.estimator.predict_proba(matrix)[:, 1]

    def _filled(self, vector: np.ndarray) -> np.ndarray:
        """The vector with its missing numerics filled by the fitted imputer (raw units)."""

        steps = self.estimator.named_steps
        row = vector.reshape(1, -1)
        scaler = steps["scale"]
        return scaler.inverse_transform(steps["impute"].transform(scaler.transform(row)))[0]

    def _scores(
        self, record: dict[str, object], vector: np.ndarray, probability: float
    ) -> dict[str, float]:
        """Signed contribution of every input to this prediction."""

        if self.explain == "linear":
            scaler = self.estimator.named_steps["scale"]
            model = self.estimator.named_steps["model"]
            vector = self._filled(vector)
            scaled = (vector - scaler.mean_) / np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
            raw = model.coef_[0] * scaled
            scores: dict[str, float] = {}
            column = 0
            for name in self.spec.numeric:
                scores[name] = float(raw[column])
                column += 1
            for name, categories in self.spec.categorical.items():
                scores[name] = float(raw[column : column + len(categories)].sum())
                column += len(categories)
            return scores

        # Any other model: put the input back to its training default and see how
        # this patient's probability moves. Positive = the input raised the risk.
        names = self.spec.inputs
        variants = np.vstack([self.spec.encode({**record, name: None})[0] for name in names])
        replaced = self._probability(variants)
        return {name: probability - float(p) for name, p in zip(names, replaced)}

    def predict(self, record: dict[str, object], pseudonym: str | None = None) -> Prediction:
        vector, imputed = self.spec.encode(record)
        unrecognized = [
            name
            for name, categories in self.spec.categorical.items()
            if record.get(name) is not None and record[name] not in categories
        ]
        probability = float(self._probability(vector.reshape(1, -1))[0])
        scores = self._scores(record, vector, probability)
        # Shown values: a missing numeric appears with the value the imputer gave it.
        vector = self._filled(vector)

        contributions: list[Contribution] = []
        column = 0
        for name in self.spec.numeric:
            contributions.append(
                Contribution(
                    feature=name,
                    value=float(vector[column]),
                    contribution=round(scores[name], 4),
                    imputed=name in imputed,
                )
            )
            column += 1
        for name, categories in self.spec.categorical.items():
            block = slice(column, column + len(categories))
            chosen = next(
                (c for c, flag in zip(categories, vector[block]) if flag == 1.0), "unrecognized"
            )
            contributions.append(
                Contribution(
                    feature=name,
                    value=chosen,
                    contribution=round(scores[name], 4),
                    imputed=name in imputed,
                )
            )
            column += len(categories)
        contributions.sort(key=lambda c: -abs(c.contribution))

        positive = probability >= self.threshold
        return Prediction(
            probability=round(probability, 4),
            nivel=self.positive_label if positive else self.negative_label,
            positive=positive,
            threshold=self.threshold,
            model_version=self.version,
            model_family=self.family,
            explanation=LINEAR_EXPLANATION if self.explain == "linear" else OCCLUSION_EXPLANATION,
            imputed=imputed,
            unrecognized=unrecognized,
            top_features=contributions[:TOP_FEATURES],
            pseudonym=pseudonym,
        )


class MinutesPrediction(BaseModel):
    minutes: float
    model_version: str
    model_family: str
    typical_error_minutes: float
    imputed: list[str]
    unrecognized: list[str]


class MinutesBundle:
    """Regression on the continuous target: minutes until the bed is ready."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.version: str = raw["version"]
        self.family: str = raw["family"]
        self.spec = raw["spec"]
        self.estimator = raw["estimator"]
        self.mae_cv: float = raw["mae_cv"]
        self.low, self.high = raw["range"]

    def predict(self, record: dict[str, object]) -> MinutesPrediction:
        vector, imputed = self.spec.encode(record)
        unrecognized = [
            name
            for name, categories in self.spec.categorical.items()
            if record.get(name) is not None and record[name] not in categories
        ]
        minutes = float(self.estimator.predict(vector.reshape(1, -1))[0])
        return MinutesPrediction(
            minutes=round(min(max(minutes, self.low), self.high), 1),
            model_version=self.version,
            model_family=self.family,
            typical_error_minutes=self.mae_cv,
            imputed=imputed,
            unrecognized=unrecognized,
        )


def _cleaning_record(body: CleaningRequest) -> dict[str, object]:
    record: dict[str, object] = {
        key: (int(value) if isinstance(value, bool) else value)
        for key, value in body.model_dump().items()
    }
    record["shift"] = shift_of(body.release_hour)
    if record["service"] is not None and not str(record["service"]).strip():
        raise HTTPException(status_code=422, detail="service must not be blank")
    return record


def create_app(artifact_dir: Path = DEFAULT_ARTIFACTS) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        app.state.criticality = Bundle(joblib.load(artifact_dir / "criticality.joblib"))
        app.state.cleaning = Bundle(joblib.load(artifact_dir / "cleaning_delay.joblib"))
        app.state.minutes = MinutesBundle(joblib.load(artifact_dir / "cleaning_minutes.joblib"))
        app.state.metrics = json.loads((artifact_dir / "metrics.json").read_text("utf-8"))
        yield

    app = FastAPI(
        title="SaludRed ML",
        version="1.0.0",
        description="Prediction service. It has no database and accepts no identifiers.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "models": {
                "criticality": request.app.state.criticality.version,
                "cleaning_delay": request.app.state.cleaning.version,
            },
        }

    @app.get("/models")
    def models(request: Request) -> dict[str, Any]:
        return request.app.state.metrics

    @app.post("/predict/criticality", response_model=Prediction)
    def predict_criticality(body: CriticalityRequest, request: Request) -> Prediction:
        record: dict[str, object] = body.model_dump(exclude={"pseudonym"})
        return request.app.state.criticality.predict(record, pseudonym=body.pseudonym)

    @app.post("/predict/cleaning-delay", response_model=Prediction)
    def predict_cleaning(body: CleaningRequest, request: Request) -> Prediction:
        return request.app.state.cleaning.predict(_cleaning_record(body))

    @app.post("/predict/cleaning-minutes", response_model=MinutesPrediction)
    def predict_cleaning_minutes(body: CleaningRequest, request: Request) -> MinutesPrediction:
        return request.app.state.minutes.predict(_cleaning_record(body))

    return app


app = create_app()
