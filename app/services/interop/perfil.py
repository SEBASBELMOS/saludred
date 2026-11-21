"""The common profile every team validates (slide 27).

Connecting is not understanding each other: two systems can exchange valid FHIR and
still mean different things. The profile fixes the vocabulary:

* LOINC (``http://loinc.org``) for what was measured: 2339-0 glucose, 4548-4 HbA1c,
  39156-5 BMI, 8480-6 and 8462-4 blood pressure.
* SNOMED CT (``http://snomed.info/sct``): 44054006 type 2 diabetes, 4855003 diabetic
  retinopathy, 38341003 hypertension, 3457005 referral.
* UCUM (``http://unitsofmeasure.org``) for units.

The validator rejects a misspelled LOINC system, a unit that is not the profile's and a
value outside the plausible range (a typing error, not a sick patient). Every problem
becomes an ``Issue`` for the ``OperationOutcome`` of a 422.

POR CONFIRMAR: the slides name the codes but not the plausible ranges of HbA1c and BMI
nor the critical thresholds; the values below are proposals (marked ``to_confirm``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import get_settings
from app.models import loinc
from app.services.interop import federado
from app.services.interop.federado import looks_like_federated_id
from app.services.interop.outcome import Issue

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"

GLUCOSE_CODE = loinc.GLUCOSE.code
REFERRAL_CODE = "3457005"


@dataclass(frozen=True)
class ObservationProfile:
    code: str
    name: str
    unit: str  # UCUM
    low: float
    high: float
    to_confirm: bool = False


PROFILE: dict[str, ObservationProfile] = {
    # Glucose and blood pressure reuse the plausible ranges of the clinical catalog.
    "2339-0": ObservationProfile("2339-0", "Glucosa", "mg/dL", *loinc.PLAUSIBLE_RANGE["2339-0"]),
    "8480-6": ObservationProfile(
        "8480-6", "Presion arterial sistolica", "mm[Hg]", *loinc.PLAUSIBLE_RANGE["8480-6"]
    ),
    "8462-4": ObservationProfile(
        "8462-4", "Presion arterial diastolica", "mm[Hg]", *loinc.PLAUSIBLE_RANGE["8462-4"]
    ),
    # Proposals, not in the sources.
    "4548-4": ObservationProfile("4548-4", "Hemoglobina glicosilada", "%", 3.0, 20.0, True),
    "39156-5": ObservationProfile("39156-5", "Indice de masa corporal", "kg/m2", 10.0, 80.0, True),
}

SNOMED_CODES: dict[str, str] = {
    "44054006": "Diabetes mellitus tipo 2",
    "4855003": "Retinopatia diabetica",
    "38341003": "Hipertension arterial",
    REFERRAL_CODE: "Remision",
}

OBSERVATION_STATUSES = frozenset(
    {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
     "entered-in-error", "unknown"}
)
REQUEST_STATUSES = frozenset(
    {"draft", "active", "on-hold", "revoked", "completed", "entered-in-error", "unknown"}
)
REQUEST_INTENTS = frozenset(
    {"proposal", "plan", "directive", "order", "original-order", "reflex-order",
     "filler-order", "instance-order", "option"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _codings(concept: Any) -> list[dict[str, Any]]:
    raw = _dict(concept).get("coding")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_date(resource: dict[str, Any], key: str, issues: list[Issue]) -> None:
    value = resource.get(key)
    if value is None:
        return
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        issues.append(
            Issue("value", f"{key} no es una fecha ISO 8601 valida", (f"{resource.get('resourceType')}.{key}",))
        )


def is_document_identifier(identifier: Any) -> bool:
    """A national-document Identifier that this deployment accepts instead of the federated id.

    Only with ``INTEROP_ACCEPT_DOCUMENT_IDENTIFIER`` on, and never for an Identifier of the
    federated system itself (that one keeps the strict shape check).
    """

    settings = get_settings()
    return (
        settings.interop_accept_document_identifier
        and isinstance(identifier, dict)
        and identifier.get("system") != settings.federated_system
        and federado.document_parts(identifier) is not None
    )


def federated_id_of(identifier: dict[str, Any]) -> str:
    """The federated id an Identifier stands for: its value, or the one computed from a document.

    ``""`` when a document cannot be turned into an id (no ``FEDERATED_SECRET``); the receiver
    answers 503 in that case before it uses the value.
    """

    if is_document_identifier(identifier):
        return federado.federated_id_of_document(identifier) or ""
    return str(identifier.get("value"))


def _check_subject(resource: dict[str, Any], issues: list[Issue]) -> None:
    kind = resource.get("resourceType")
    where = f"{kind}.subject.identifier"
    identifier = _dict(_dict(resource.get("subject")).get("identifier"))
    if not identifier:
        issues.append(
            Issue("required", "subject.identifier es obligatorio: el paciente se identifica "
                  "con el identificador federado, no con una referencia local", (where,))
        )
        return
    if is_document_identifier(identifier):
        if federado.enabled() and federado.federated_id_of_document(identifier) is None:
            issues.append(Issue("value", "El documento de subject.identifier no es valido "
                                f"(tipo {', '.join(federado.TYPES)} y numero)", (f"{where}.value",)))
        return
    expected = get_settings().federated_system
    if identifier.get("system") != expected:
        issues.append(
            Issue("code-invalid", f"{where}.system debe ser {expected}", (f"{where}.system",))
        )
    if not looks_like_federated_id(identifier.get("value")):
        issues.append(
            Issue("value", "El identificador federado debe tener 32 caracteres hexadecimales "
                  "en minuscula", (f"{where}.value",))
        )


def validate_observation(resource: dict[str, Any]) -> list[Issue]:
    """Issues of an Observation against the profile; empty means it conforms."""

    issues: list[Issue] = []
    if resource.get("resourceType") != "Observation":
        return [Issue("structure", "El recurso debe ser una Observation", ("resourceType",))]

    if resource.get("status") not in OBSERVATION_STATUSES:
        issues.append(
            Issue("value", "status obligatorio, uno de: " + ", ".join(sorted(OBSERVATION_STATUSES)),
                  ("Observation.status",))
        )
    _check_subject(resource, issues)
    _check_date(resource, "effectiveDateTime", issues)

    # Code: the first coding that carries a code decides.
    codings = [c for c in _codings(resource.get("code")) if c.get("code")]
    profile: ObservationProfile | None = None
    if not codings:
        issues.append(Issue("required", "code.coding con un codigo LOINC es obligatorio",
                            ("Observation.code.coding",)))
    else:
        coding = codings[0]
        code = str(coding.get("code")).strip()
        where = "Observation.code.coding[0]"
        if code in PROFILE and coding.get("system") != LOINC:
            issues.append(
                Issue("code-invalid", f"Sistema de codificacion invalido para {code}: "
                      f"se esperaba {LOINC} y llego {coding.get('system')!r}", (f"{where}.system",))
            )
        elif code not in PROFILE:
            issues.append(
                Issue("code-invalid", f"El codigo {code} no esta en el perfil comun "
                      f"({', '.join(PROFILE)})", (f"{where}.code",))
            )
        elif coding.get("system") == LOINC:
            profile = PROFILE[code]

    quantity = _dict(resource.get("valueQuantity"))
    if not quantity:
        issues.append(Issue("required", "valueQuantity es obligatorio", ("Observation.valueQuantity",)))
        return issues
    value = quantity.get("value")
    if not _is_number(value):
        issues.append(Issue("value", "valueQuantity.value debe ser un numero",
                            ("Observation.valueQuantity.value",)))
    if quantity.get("system") != UCUM:
        issues.append(
            Issue("code-invalid", f"valueQuantity.system debe ser {UCUM}",
                  ("Observation.valueQuantity.system",))
        )
    if profile is not None:
        if quantity.get("code") != profile.unit:
            issues.append(
                Issue("code-invalid", f"Unidad invalida para {profile.code}: se esperaba "
                      f"{profile.unit} y llego {quantity.get('code')!r}",
                      ("Observation.valueQuantity.code",))
            )
        elif _is_number(value) and not profile.low <= float(value) <= profile.high:
            issues.append(
                Issue("value", f"Valor implausible para {profile.code}: {value} {profile.unit} "
                      f"(rango plausible {profile.low:g} a {profile.high:g})",
                      ("Observation.valueQuantity.value",))
            )
    return issues


def reason_of(resource: dict[str, Any]) -> str:
    """The reason of a ServiceRequest as text (``reasonCode`` text, display or code)."""

    parts: list[str] = []
    reasons = resource.get("reasonCode")
    for reason in reasons if isinstance(reasons, list) else []:
        text = str(_dict(reason).get("text") or "").strip()
        if text:
            parts.append(text)
            continue
        for coding in _codings(reason):
            label = str(coding.get("display") or "").strip()
            code = str(coding.get("code") or "").strip()
            if label or code:
                parts.append(label or SNOMED_CODES.get(code, code))
                break
    return "; ".join(parts)[:1000]


def validate_service_request(resource: dict[str, Any]) -> list[Issue]:
    """Issues of a referral ServiceRequest against the profile."""

    issues: list[Issue] = []
    if resource.get("resourceType") != "ServiceRequest":
        return [Issue("structure", "El recurso debe ser una ServiceRequest", ("resourceType",))]

    if resource.get("status") not in REQUEST_STATUSES:
        issues.append(Issue("value", "status obligatorio, uno de: " + ", ".join(sorted(REQUEST_STATUSES)),
                            ("ServiceRequest.status",)))
    if resource.get("intent") not in REQUEST_INTENTS:
        issues.append(Issue("value", "intent obligatorio (por ejemplo order)", ("ServiceRequest.intent",)))
    _check_subject(resource, issues)
    _check_date(resource, "authoredOn", issues)

    # The referral code: SNOMED 3457005 with the SNOMED system.
    codings = _codings(resource.get("code"))
    if not any(c.get("system") == SNOMED and str(c.get("code")) == REFERRAL_CODE for c in codings):
        misspelled = any(str(c.get("code")) == REFERRAL_CODE for c in codings)
        issues.append(
            Issue("code-invalid",
                  (f"El codigo {REFERRAL_CODE} llego con un sistema distinto de {SNOMED}"
                   if misspelled else
                   f"code.coding debe incluir SNOMED {REFERRAL_CODE} (remision) con el sistema {SNOMED}"),
                  ("ServiceRequest.code.coding",))
        )

    # Reasons: the SNOMED codes that carry the profile's system must be in the list.
    reasons = resource.get("reasonCode")
    for i, reason in enumerate(reasons if isinstance(reasons, list) else []):
        for j, coding in enumerate(_codings(reason)):
            code = str(coding.get("code") or "")
            where = f"ServiceRequest.reasonCode[{i}].coding[{j}]"
            if code in SNOMED_CODES and coding.get("system") != SNOMED:
                issues.append(Issue("code-invalid", f"El codigo {code} llego con un sistema distinto "
                                    f"de {SNOMED}", (f"{where}.system",)))
            elif coding.get("system") == SNOMED and code not in SNOMED_CODES:
                issues.append(Issue("code-invalid", f"El codigo SNOMED {code} no esta en el perfil comun "
                                    f"({', '.join(SNOMED_CODES)})", (f"{where}.code",)))
    if not reason_of(resource):
        issues.append(Issue("required", "reasonCode con el motivo de la remision es obligatorio "
                            "(texto o un coding SNOMED del perfil)", ("ServiceRequest.reasonCode",)))
    return issues


def subject_federated_id(resource: dict[str, Any]) -> str:
    """The federated id of a resource that already passed ``_check_subject``."""

    return federated_id_of(_dict(_dict(resource.get("subject")).get("identifier")))


def canonical_resource(resource: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``resource`` with every document Identifier of the patient replaced by the
    federated id it stands for, so the number never reaches ``fhir_inbound_events``.

    It runs whatever the setting says. A Patient that carries the federated id AND a document
    Identifier (accepted in strict mode too, because the federated id is enough) loses the
    document; with ``INTEROP_ACCEPT_DOCUMENT_IDENTIFIER`` on, a lone document is converted to
    the federated id it stands for. Handles a Bundle (every entry), a Patient (its identifiers)
    and any resource with a ``subject``.
    """

    system = get_settings().federated_system

    def converted(identifier: Any) -> Any:
        if not is_document_identifier(identifier) or not federado.enabled():
            return identifier
        value = federado.federated_id_of_document(identifier)
        return {"system": system, "value": value} if value else identifier

    def is_raw_document(identifier: Any) -> bool:
        return (
            isinstance(identifier, dict)
            and identifier.get("system") != system
            and federado.document_parts(identifier) is not None
        )

    def convert(item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item)
        if out.get("resourceType") == "Bundle" and isinstance(out.get("entry"), list):
            out["entry"] = [
                {**entry, "resource": convert(entry["resource"])}
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict) else entry
                for entry in out["entry"]
            ]
            return out
        if out.get("resourceType") == "Patient" and isinstance(out.get("identifier"), list):
            identifiers: list[Any] = []
            federated_seen: set[str] = set()
            has_federated = any(
                isinstance(i, dict) and i.get("system") == system and looks_like_federated_id(i.get("value"))
                for i in out["identifier"]
            )
            for identifier in (converted(i) for i in out["identifier"]):
                if has_federated and is_raw_document(identifier):
                    continue  # the federated id is enough: the number is never stored
                if isinstance(identifier, dict) and identifier.get("system") == system:
                    if str(identifier.get("value")) in federated_seen:
                        continue  # the document and the federated id of the same patient
                    federated_seen.add(str(identifier.get("value")))
                identifiers.append(identifier)
            out["identifier"] = identifiers
        subject = out.get("subject")
        if isinstance(subject, dict) and isinstance(subject.get("identifier"), dict):
            out["subject"] = {**subject, "identifier": converted(subject["identifier"])}
        return out

    return convert(resource)


def observation_numbers(resource: dict[str, Any]) -> tuple[str, float]:
    """``(LOINC code, value)`` of an Observation that already conforms."""

    code = str(_codings(resource.get("code"))[0].get("code")).strip()
    return code, float(_dict(resource.get("valueQuantity"))["value"])


def performer_organization_code(resource: dict[str, Any]) -> str | None:
    """The IPS the referral is addressed to: ``performer[].identifier.value``.

    SaludRed matches it against ``organizations.code`` (a partner learns the code when it
    registers). The first performer that carries an identifier wins.
    """

    performers = resource.get("performer")
    for performer in performers if isinstance(performers, list) else []:
        value = _dict(_dict(performer).get("identifier")).get("value")
        if value:
            return str(value).strip()
    return None
