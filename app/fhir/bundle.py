"""Clinical record -> FHIR R4 ``Bundle`` (type ``collection``).

Pure functions, like ``mappers``: they build JSON from already-loaded rows.
Entries reference each other with ``urn:uuid:`` full URLs, so the Bundle is
self-contained and can be loaded into any FHIR server. The identifiers are the
same ones the synchronization to HAPI uses, which makes a re-import or a later
sync match instead of duplicating.

AI reports: an approved report is a probabilistic prediction about the patient,
which is exactly what ``RiskAssessment`` models (``prediction.probabilityDecimal``,
``prediction.qualitativeRisk``, ``method``). ``DiagnosticReport`` was rejected:
it conveys results of diagnostic investigations (lab, imaging) with a
conclusion, and an AI risk score is neither a test nor a diagnosis -- calling it
a diagnostic report would overstate it. Only reports a clinician approved are
exported, the same rule as the clinical record.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.fhir import mappers
from app.models.clinical import Encounter, Observation
from app.models.patient import Patient
from app.schemas.ai import ClinicalRecordReport
from app.services.interop.perfil import LOINC, PROFILE

RISK_PROBABILITY = "http://terminology.hl7.org/CodeSystem/risk-probability"


def minimal_partner_bundle(
    patient: Patient,
    observations: Sequence[Observation],
    approved_reports: Sequence[dict],
    *,
    generated_at: datetime | None = None,
    service_request: dict[str, Any] | None = None,
    federated_id: str | None = None,
    document_identifier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a partner collection from scoped rows and already-approved reports.

    The caller owns report approval. Reports are resource-agnostic; duplicate
    identities (or identical resources without an id) are emitted only once.
    Local patient and encounter references never cross the partner boundary.

    ``service_request`` (R30) is the referral order that travels with the evidence.
    ``federated_id`` is used only when the patient row has no stored federated id;
    without either, the Bundle carries no patient identifier at all.
    ``document_identifier`` (any-partner mode, INTEROP_SEND_DOCUMENT_IDENTIFIER) adds the
    national document to the Patient ONLY, next to the federated id: a negotiated
    minimization trade-off for partners that cannot compute the federated id. The subjects
    of the other resources keep the federated id alone.
    """
    value = patient.id_federado or federated_id
    identifier = (
        {"system": get_settings().federated_system, "value": value} if value else None
    )
    subject = {"identifier": identifier} if identifier else {}
    minimal_patient: dict[str, Any] = {"resourceType": "Patient"}
    if identifier:
        minimal_patient["identifier"] = [identifier]
        if document_identifier:
            minimal_patient["identifier"].append(deepcopy(document_identifier))
    entries = [{"resource": minimal_patient}]
    if service_request is not None:
        order = deepcopy(service_request)
        order["subject"] = deepcopy(subject)
        entries.append({"resource": order})
    for observation in observations:
        if (observation.patient_id != patient.id or observation.deleted_at is not None
                or observation.code_system != LOINC or observation.code not in PROFILE):
            continue
        resource = mappers.observation_to_fhir(
            observation, identifier_system=get_settings().fhir_identifier_system,
            patient_ref="", encounter_ref="",
        )
        resource.pop("encounter", None)
        resource["subject"] = deepcopy(subject)
        entries.append({"resource": resource})
    seen: set[tuple[str, str]] = set()
    for report in approved_reports:
        key = (report["resourceType"], str(report["id"]) if report.get("id")
               else json.dumps(report, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        resource = deepcopy(report)
        resource["subject"] = deepcopy(subject)
        resource.pop("encounter", None)
        entries.append({"resource": resource})
    return {
        "resourceType": "Bundle", "type": "collection",
        # No ``total``: FHIR invariant bdl-1 allows it only on searchset and history Bundles.
        "timestamp": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "entry": entries,
    }


def full_url(resource_id: object) -> str:
    return f"urn:uuid:{resource_id}"


def risk_assessment(
    report: ClinicalRecordReport, *, identifier_system: str, patient_ref: str
) -> dict[str, Any]:
    critical = report.nivel == "critico"
    resource: dict[str, Any] = {
        "resourceType": "RiskAssessment",
        "id": str(report.id),
        "identifier": [
            {"system": f"{identifier_system}:ai-reports", "value": str(report.id)}
        ],
        "status": "final",
        "method": {"text": f"Modelo de IA {report.model_version} (criticidad del paciente)"},
        "subject": {"reference": patient_ref},
        "occurrenceDateTime": (report.reviewed_at or report.created_at).isoformat(),
        "prediction": [
            {
                "outcome": {"text": "Paciente critico" if critical else "Paciente no critico"},
                "probabilityDecimal": report.probability,
                "qualitativeRisk": {
                    "coding": [
                        {
                            "system": RISK_PROBABILITY,
                            "code": "high" if critical else "low",
                            "display": "High likelihood" if critical else "Low likelihood",
                        }
                    ]
                },
            }
        ],
    }
    if report.firmado_por:
        resource["performer"] = {"display": report.firmado_por}
    notes: list[dict[str, str]] = []
    if report.top_features:
        drivers = ", ".join(f"{f.feature} ({f.contribution:+.2f})" for f in report.top_features)
        notes.append({"text": f"Variables que mas pesaron: {drivers}"})
    if report.observaciones:
        notes.append({"text": f"Observaciones del clinico: {report.observaciones}"})
    if notes:
        resource["note"] = notes
    return mappers.with_origin_tag(resource, identifier_system)


def clinical_bundle(
    patient: Patient,
    encounters: list[Encounter],
    observations: list[Observation],
    reports: list[ClinicalRecordReport],
    *,
    identifier_system: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    patient_ref = full_url(patient.id)
    entries: list[dict[str, Any]] = []

    resource = mappers.patient_to_fhir(patient, identifier_system=identifier_system)
    resource["id"] = str(patient.id)
    entries.append({"fullUrl": patient_ref, "resource": resource})

    for encounter in encounters:
        resource = mappers.encounter_to_fhir(
            encounter, identifier_system=identifier_system, patient_ref=patient_ref
        )
        resource["id"] = str(encounter.id)
        entries.append({"fullUrl": full_url(encounter.id), "resource": resource})

    # The encounter of an observation may be outside the exported list (scope
    # or the cap): then the reference is simply omitted instead of dangling.
    exported = {e.id for e in encounters}
    for observation in observations:
        resource = mappers.observation_to_fhir(
            observation,
            identifier_system=identifier_system,
            patient_ref=patient_ref,
            encounter_ref=full_url(observation.encounter_id),
        )
        if observation.encounter_id not in exported:
            resource.pop("encounter", None)
        resource["id"] = str(observation.id)
        entries.append({"fullUrl": full_url(observation.id), "resource": resource})

    for report in reports:
        resource = risk_assessment(
            report, identifier_system=identifier_system, patient_ref=patient_ref
        )
        entries.append({"fullUrl": full_url(report.id), "resource": resource})

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": (generated_at or datetime.now(timezone.utc)).isoformat(),
        # No ``total``: FHIR invariant bdl-1 allows it only on searchset and history Bundles.
        "entry": entries,
    }
