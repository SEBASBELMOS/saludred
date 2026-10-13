"""FHIR R4 Observation -> SaludRed fields.

Pure functions, shared by the rest-hook (R21) and the Bundle import (G): both
receive an Observation from outside and need the same mapping. Nothing here
touches the database or the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.enums import ObservationStatus

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"

# Largest magnitude Numeric(12, 3) can store.
MAX_NUMERIC = Decimal("999999999.999")

# Spanish names for the codes the system sees most. Anything else shows the
# display sent by the source. Codes are LOINC.
SPANISH_LABELS = {
    "2339-0": "Glucosa",
    "2345-7": "Glucosa",
    "15074-8": "Glucosa",
    "4548-4": "Hemoglobina glicosilada",
    "718-7": "Hemoglobina",
    "2160-0": "Creatinina",
    "6690-2": "Leucocitos",
    "8867-4": "Frecuencia cardiaca",
    "8310-5": "Temperatura corporal",
    "9279-1": "Frecuencia respiratoria",
    "59408-5": "Saturacion de oxigeno",
    "8480-6": "Presion arterial sistolica",
    "8462-4": "Presion arterial diastolica",
}

_STATUS_MAP = {
    "registered": ObservationStatus.REGISTERED,
    "preliminary": ObservationStatus.PRELIMINARY,
    "final": ObservationStatus.FINAL,
    "amended": ObservationStatus.AMENDED,
    "corrected": ObservationStatus.AMENDED,
    "cancelled": ObservationStatus.CANCELLED,
    "entered-in-error": ObservationStatus.CANCELLED,
    "unknown": ObservationStatus.PRELIMINARY,
}


class FhirParseError(ValueError):
    """The resource cannot be mapped; the message says why."""


@dataclass(frozen=True)
class ParsedObservation:
    code_system: str
    code: str
    display: str
    status: ObservationStatus
    value_numeric: Decimal | None
    value_text: str | None
    unit: str | None
    observed_at: datetime

    @property
    def has_value(self) -> bool:
        return self.value_numeric is not None or self.value_text is not None

    @property
    def label(self) -> str:
        return SPANISH_LABELS.get(self.code, self.display)

    @property
    def value_label(self) -> str:
        if self.value_numeric is not None:
            number = format(self.value_numeric.normalize(), "f")
            return f"{number} {self.unit}" if self.unit else number
        return self.value_text or ""

    def describe(self) -> str:
        """``Glucosa 182 mg/dL`` -- the readable form used in messages."""

        value = self.value_label
        return f"{self.label} {value}" if value else f"{self.label} (sin valor)"


@dataclass(frozen=True)
class SubjectRef:
    """How an Observation points at its patient."""

    fhir_id: str | None = None
    uuid_ref: str | None = None  # urn:uuid:... (Bundle-internal reference)
    identifier_system: str | None = None
    identifier_value: str | None = None


_PATIENT_REF = re.compile(r"(?:^|/)Patient/([A-Za-z0-9\-.]{1,64})(?:/_history/[^/]+)?$")


def parse_subject(resource: dict[str, Any]) -> SubjectRef | None:
    subject = resource.get("subject")
    if not isinstance(subject, dict):
        return None
    reference = subject.get("reference")
    if isinstance(reference, str):
        if reference.startswith("urn:uuid:"):
            return SubjectRef(uuid_ref=reference)
        match = _PATIENT_REF.search(reference)
        if match:
            return SubjectRef(fhir_id=match.group(1))
    identifier = subject.get("identifier")
    if isinstance(identifier, dict) and identifier.get("value"):
        return SubjectRef(
            identifier_system=identifier.get("system"),
            identifier_value=str(identifier["value"]),
        )
    return None


def parse_datetime(value: str) -> datetime:
    text = value.strip()
    if re.fullmatch(r"\d{4}", text):
        text = f"{text}-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text = f"{text}-01"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FhirParseError(f"Fecha invalida: {value!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _observed_at(resource: dict[str, Any]) -> datetime:
    candidates = [
        resource.get("effectiveDateTime"),
        resource.get("effectiveInstant"),
        (resource.get("effectivePeriod") or {}).get("start"),
        resource.get("issued"),
        (resource.get("meta") or {}).get("lastUpdated"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return parse_datetime(candidate)
    return datetime.now(timezone.utc)


def _code(resource: dict[str, Any]) -> tuple[str, str, str]:
    concept = resource.get("code")
    if not isinstance(concept, dict):
        raise FhirParseError("Observation.code es obligatorio")
    codings = [c for c in concept.get("coding", []) or [] if isinstance(c, dict) and c.get("code")]
    if not codings:
        raise FhirParseError("Observation.code necesita al menos un coding con code")
    chosen = next((c for c in codings if c.get("system") == LOINC), codings[0])
    code = str(chosen["code"]).strip()
    display = str(chosen.get("display") or concept.get("text") or code).strip()
    return str(chosen.get("system") or LOINC), code[:40], display[:200]


def _quantity(value: Any) -> tuple[Decimal | None, str | None]:
    if not isinstance(value, dict) or value.get("value") is None:
        return None, None
    try:
        number = Decimal(str(value["value"]))
    except InvalidOperation as exc:
        raise FhirParseError("valueQuantity.value no es numerico") from exc
    if not number.is_finite() or abs(number) > MAX_NUMERIC:
        raise FhirParseError("valueQuantity.value fuera del rango almacenable")
    number = number.quantize(Decimal("0.001"))
    unit = value.get("code") or value.get("unit") or "1"
    return number, str(unit)[:40]


def parse_observation(resource: dict[str, Any]) -> ParsedObservation:
    """Map an Observation. Raises :class:`FhirParseError` when it has no usable code."""

    if resource.get("resourceType") != "Observation":
        raise FhirParseError("El recurso no es una Observation")
    system, code, display = _code(resource)

    numeric: Decimal | None = None
    text: str | None = None
    unit: str | None = None
    if "valueQuantity" in resource:
        numeric, unit = _quantity(resource["valueQuantity"])
    elif "valueInteger" in resource:
        numeric, unit = _quantity({"value": resource["valueInteger"]})
    elif isinstance(resource.get("valueString"), str):
        text = resource["valueString"].strip()[:500] or None
    elif isinstance(resource.get("valueCodeableConcept"), dict):
        concept = resource["valueCodeableConcept"]
        coding = (concept.get("coding") or [{}])[0]
        text = str(concept.get("text") or coding.get("display") or coding.get("code") or "")[:500] or None

    raw_status = str(resource.get("status", "final"))
    return ParsedObservation(
        code_system=system[:200],
        code=code,
        display=display,
        status=_STATUS_MAP.get(raw_status, ObservationStatus.FINAL),
        value_numeric=numeric,
        value_text=text,
        unit=unit,
        observed_at=_observed_at(resource),
    )
