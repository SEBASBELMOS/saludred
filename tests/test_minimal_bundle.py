"""Partner minimization, with the full clinical export kept intact."""

from copy import deepcopy
from datetime import datetime, timezone
import uuid

import pytest

from app.core.config import get_settings
from app.fhir import mappers
from app.fhir.bundle import clinical_bundle, full_url, minimal_partner_bundle, risk_assessment
from app.models.enums import AiReportKind, AiReportStatus
from app.schemas.ai import ClinicalRecordReport
from app.services.interop.perfil import PROFILE
from tests.factories import make_encounter, make_observation, make_patient

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def test_partner_patient_has_only_federated_identifier():
    patient = make_patient(id_federado="a" * 32)
    bundle = minimal_partner_bundle(patient, [], [], generated_at=NOW)
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    assert bundle["entry"] == [{"resource": {
        "resourceType": "Patient",
        "identifier": [{"system": get_settings().federated_system, "value": "a" * 32}],
    }}]
    assert "total" not in bundle  # FHIR bdl-1: only searchset and history Bundles may carry it


def test_partner_patient_without_federated_id_has_no_local_fallback():
    bundle = minimal_partner_bundle(make_patient(id_federado=None), [], [])
    assert bundle["entry"][0]["resource"] == {"resourceType": "Patient"}


def test_partner_includes_only_profile_observations_for_this_patient():
    patient = make_patient(id_federado="a" * 32)
    observations = [make_observation(patient_id=patient.id, code=code) for code in PROFILE]
    excluded = [
        make_observation(patient_id=patient.id),
        make_observation(patient_id=patient.id, code="2339-0", code_system="urn:other"),
        make_observation(code="2339-0"),
        make_observation(patient_id=patient.id, code="2339-0", deleted_at=NOW),
    ]
    bundle = minimal_partner_bundle(patient, observations + excluded, [])
    resources = [entry["resource"] for entry in bundle["entry"]]
    assert [r["resourceType"] for r in resources] == ["Patient"] + ["Observation"] * len(PROFILE)
    assert {r["code"]["coding"][0]["code"] for r in resources[1:]} == set(PROFILE)
    for resource in resources[1:]:
        assert "encounter" not in resource
        assert resource["subject"] == {"identifier": resources[0]["identifier"][0]}


@pytest.mark.parametrize("kind", ["RiskAssessment", "DiagnosticReport"])
def test_partner_emits_each_approved_report_once_without_mutating_input(kind):
    patient = make_patient(id_federado="a" * 32)
    report = {"resourceType": kind, "id": "report-1", "status": "final",
              "subject": {"reference": "Patient/internal"}, "encounter": {"reference": "Encounter/local"}}
    original = deepcopy(report)
    bundle = minimal_partner_bundle(patient, [], [report, deepcopy(report)])
    assert "total" not in bundle and len(bundle["entry"]) == 2
    resource = bundle["entry"][1]["resource"]
    assert resource["resourceType"] == kind
    assert resource["subject"] == {"identifier": bundle["entry"][0]["resource"]["identifier"][0]}
    assert "encounter" not in resource
    assert report == original


def test_clinical_bundle_output_is_unchanged():
    patient = make_patient(id_federado="a" * 32)
    encounter = make_encounter(patient_id=patient.id)
    observation = make_observation(patient_id=patient.id, encounter_id=encounter.id)
    report = ClinicalRecordReport(
        id=uuid.uuid4(), kind=AiReportKind.CRITICALITY, estado=AiReportStatus.APPROVED,
        probability=0.91, nivel="critico", model_version="test", top_features=[],
        observaciones="Approved", firmado_por="Reviewer", reviewed_at=NOW, created_at=NOW,
    )
    system = "urn:test:saludred"
    patient_ref = full_url(patient.id)
    resources = [
        mappers.patient_to_fhir(patient, identifier_system=system),
        mappers.encounter_to_fhir(encounter, identifier_system=system, patient_ref=patient_ref),
        mappers.observation_to_fhir(observation, identifier_system=system,
                                    patient_ref=patient_ref, encounter_ref=full_url(encounter.id)),
    ]
    entries = []
    for row, resource in zip([patient, encounter, observation], resources, strict=True):
        resource["id"] = str(row.id)
        entries.append({"fullUrl": full_url(row.id), "resource": resource})
    entries.append({"fullUrl": full_url(report.id), "resource": risk_assessment(
        report, identifier_system=system, patient_ref=patient_ref,
    )})
    expected = {"resourceType": "Bundle", "type": "collection", "timestamp": NOW.isoformat(),
                "entry": entries}  # no ``total``: bdl-1
    assert clinical_bundle(patient, [encounter], [observation], [report],
                           identifier_system=system, generated_at=NOW) == expected
    minimal_partner_bundle(patient, [observation], [], generated_at=NOW)
    assert clinical_bundle(patient, [encounter], [observation], [report],
                           identifier_system=system, generated_at=NOW) == expected
    assert "telecom" in resources[0] and "address" in resources[0]
