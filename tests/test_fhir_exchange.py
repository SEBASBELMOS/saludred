# ruff: noqa: F811
"""Clinical record as a FHIR R4 Bundle: export (G) and import (G)."""

from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Encounter, Observation, Patient
from app.models.enums import AuditAction, ObservationOrigin
from tests.test_ai_reports import CANARY, VITALS, FakeMl, _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

SYSTEM = "urn:saludred:identifier"


@pytest.fixture
def record(db, client_as, ml):
    patient = _create_patient(db, client_as)
    doctor = client_as("operador.norte")
    approved = _analyze(doctor, patient["id"]).json()["id"]
    _analyze(doctor, patient["id"])  # stays pending
    rejected = _analyze(doctor, patient["id"]).json()["id"]
    doctor.post(f"/api/v1/ai-reports/{approved}/approve", json={"observaciones": "Concuerda"})
    doctor.post(f"/api/v1/ai-reports/{rejected}/reject", json={"motivo": "No aplica"})
    return db, client_as, patient, approved


def _kinds(bundle: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in bundle["entry"]:
        kind = entry["resource"]["resourceType"]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


# -- export -----------------------------------------------------------------


def test_bundle_structure(record) -> None:
    _db, client_as, patient, approved = record
    response = client_as("operador.norte").get(f"/api/v1/patients/{patient['id']}/fhir-bundle")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/fhir+json")
    bundle = response.json()
    assert bundle["resourceType"] == "Bundle" and bundle["type"] == "collection"
    assert bundle["total"] == len(bundle["entry"])
    assert _kinds(bundle) == {
        "Patient": 1, "Encounter": 1, "Observation": len(VITALS), "RiskAssessment": 1,
    }

    urls = {e["fullUrl"] for e in bundle["entry"]}
    assert len(urls) == len(bundle["entry"])
    resources = {e["resource"]["resourceType"]: e["resource"] for e in reversed(bundle["entry"])}

    # Same identifiers as the HAPI sync, so import/sync match instead of duplicating.
    patient_resource = resources["Patient"]
    assert {"system": f"{SYSTEM}:documento", "value": f"CC-{CANARY['document_number']}"} in (
        patient_resource["identifier"]
    )
    assert resources["Encounter"]["identifier"][0]["system"] == f"{SYSTEM}:encounters"
    assert resources["Observation"]["identifier"][0]["system"] == f"{SYSTEM}:observations"

    # Every reference points inside the Bundle.
    for entry in bundle["entry"]:
        resource = entry["resource"]
        for ref in (resource.get("subject"), resource.get("encounter")):
            if ref:
                assert ref["reference"] in urls

    # Only the approved AI report, as a RiskAssessment.
    risk = resources["RiskAssessment"]
    assert risk["identifier"][0]["value"] == approved
    assert risk["status"] == "final" and risk["prediction"][0]["probabilityDecimal"] == pytest.approx(0.91)
    assert risk["prediction"][0]["qualitativeRisk"]["coding"][0]["code"] == "high"
    assert any("Concuerda" in n["text"] for n in risk["note"])
    glucose = next(e["resource"] for e in bundle["entry"]
                   if e["resource"]["resourceType"] == "Observation"
                   and e["resource"]["code"]["coding"][0]["code"] == "2339-0")
    assert glucose["valueQuantity"]["value"] == 182


def test_export_access_rules_and_audit(record) -> None:
    db, client_as, patient, _approved = record
    url = f"/api/v1/patients/{patient['id']}/fhir-bundle"
    for username in ("operador.norte", "especialista.norte", "admin", "coordinador.eps"):
        assert client_as(username).get(url).status_code == 200, username
    for username in ("operador.sur", "contable.eps", "paciente.demo"):
        assert client_as(username).get(url).status_code == 403, username
    assert client_as("admin").get(f"/api/v1/patients/{uuid.uuid4()}/fhir-bundle").status_code == 404

    entries = list(db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.CLINICAL_RECORD_EXPORTED)))
    assert len(entries) == 4
    assert all(e.metadata_json["format"] == "fhir-bundle" for e in entries)
    assert entries[0].metadata_json["resources"]["RiskAssessment"] == 1


def test_a_patient_exports_only_their_own_record(db, client_as) -> None:
    own = _user(db, "paciente.demo").patient_id
    assert client_as("paciente.demo").get(f"/api/v1/patients/{own}/fhir-bundle").status_code == 200


# -- import -----------------------------------------------------------------


def _bundle(*, document: str = "CC-9001001", observations: int = 2, **patient_overrides) -> dict:
    patient = {
        "resourceType": "Patient",
        "identifier": [{"system": f"{SYSTEM}:documento", "value": document}],
        "name": [{"family": "Importada", "given": ["Paula", "Andrea"]}],
        "gender": "female",
        "birthDate": "1985-04-12",
        **patient_overrides,
    }
    entries = [{"fullUrl": "urn:uuid:p1", "resource": patient}]
    values = [(("2339-0", "Glucose"), 140, "mg/dL"), (("8867-4", "Heart rate"), 76, "/min")]
    for index, ((code, display), value, unit) in enumerate(values[:observations]):
        entries.append({
            "fullUrl": f"urn:uuid:o{index}",
            "resource": {
                "resourceType": "Observation", "id": f"src-{index}", "status": "final",
                "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
                "subject": {"reference": "urn:uuid:p1"},
                "effectiveDateTime": f"2026-09-30T0{index}:00:00Z",
                "valueQuantity": {"value": value, "unit": unit, "code": unit},
            },
        })
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


IMPORT = "/api/v1/integration/fhir/import"


def test_import_happy_path_creates_patient_encounter_and_observations(db, client_as) -> None:
    operator = _user(db, "operador.norte")
    response = client_as("operador.norte").post(IMPORT, json=_bundle())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["paciente_creado"] is True
    assert (body["observaciones_creadas"], body["observaciones_duplicadas"]) == (2, 0)

    patient = db.get(Patient, uuid.UUID(body["paciente_id"]))
    assert (patient.document_number, patient.first_name, patient.last_name) == ("9001001", "Paula Andrea", "Importada")
    assert patient.responsible_clinician_id == operator.id
    assert patient.eps_organization_id == db.get(type(operator.organization), operator.organization_id).parent_organization_id
    encounter = db.get(Encounter, uuid.UUID(body["encuentro_id"]))
    assert encounter.organization_id == operator.organization_id
    rows = list(db.scalars(select(Observation).where(Observation.patient_id == patient.id)))
    assert {r.code for r in rows} == {"2339-0", "8867-4"}
    assert all(r.origin == ObservationOrigin.IMPORT and r.created_by == operator.id for r in rows)
    assert {r.fhir_resource_id for r in rows} == {"src-0", "src-1"}

    audit = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.FHIR_IMPORT))
    assert audit.metadata_json["origin"] == "import" and audit.entity_id == patient.id

    # Importing the same Bundle again changes nothing.
    again = client_as("operador.norte").post(IMPORT, json=_bundle()).json()
    assert again["paciente_creado"] is False and again["paciente_id"] == body["paciente_id"]
    assert (again["observaciones_creadas"], again["observaciones_duplicadas"]) == (0, 2)
    assert again["encuentro_id"] == body["encuentro_id"]


def test_import_round_trip_of_our_own_export_is_idempotent(record) -> None:
    db, client_as, patient, _approved = record
    exported = client_as("operador.norte").get(f"/api/v1/patients/{patient['id']}/fhir-bundle").json()
    before = db.scalar(select(func.count()).select_from(Observation))
    response = client_as("operador.norte").post(IMPORT, json=exported)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["paciente_creado"] is False and body["paciente_id"] == patient["id"]
    assert body["observaciones_creadas"] == 0 and body["observaciones_duplicadas"] == len(VITALS)
    assert body["recursos_omitidos"] == {"Encounter": 1, "RiskAssessment": 1}
    assert db.scalar(select(func.count()).select_from(Observation)) == before


def test_admin_must_say_which_ips_receives_the_import(db, client_as) -> None:
    admin = client_as("admin")
    missing = admin.post(IMPORT, json=_bundle())
    assert missing.status_code == 422 and "organization_id" in missing.text
    norte = _user(db, "operador.norte")
    ok = admin.post(f"{IMPORT}?organization_id={norte.organization_id}&eps_organization_id="
                    f"{db.get(type(norte.organization), norte.organization_id).parent_organization_id}",
                    json=_bundle(document="CC-9001002"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["paciente_creado"] is True


@pytest.mark.parametrize("username", ["especialista.norte", "contable.eps", "paciente.demo", "coordinador.eps"])
def test_import_is_only_for_admin_and_medico(client_as, username) -> None:
    assert client_as(username).post(IMPORT, json=_bundle()).status_code == 403


def test_a_medico_cannot_import_into_another_ips_patient(record) -> None:
    _db, client_as, patient, _approved = record
    bundle = _bundle(document=f"CC-{CANARY['document_number']}")
    assert client_as("operador.sur").post(IMPORT, json=bundle).status_code == 403


def _broken(mutate) -> dict:  # noqa: ANN001
    bundle = copy.deepcopy(_bundle())
    mutate(bundle)
    return bundle


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda b: b.update(resourceType="Patient"), "Bundle"),
        (lambda b: b.update(type="searchset"), "Bundle.type"),
        (lambda b: b.update(entry=[]), "vacio"),
        (lambda b: b["entry"].pop(0), "exactamente un Patient"),
        (lambda b: b["entry"].append(copy.deepcopy(b["entry"][0])), "exactamente un Patient"),
        (lambda b: b["entry"][0]["resource"].pop("birthDate"), "birthDate"),
        (lambda b: b["entry"][0]["resource"].update(gender="x"), "gender"),
        (lambda b: b["entry"][0]["resource"].update(identifier=[]), "documento"),
        (lambda b: b["entry"][0]["resource"].update(name=[]), "family y given"),
        (lambda b: b["entry"][1]["resource"].pop("code"), "code"),
        (lambda b: b["entry"][1]["resource"].pop("valueQuantity"), "valor"),
        (lambda b: b["entry"][1]["resource"].update(subject={"reference": "urn:uuid:other"}), "subject"),
        (lambda b: b["entry"][1]["resource"].pop("subject"), "subject"),
        (lambda b: b["entry"][1]["resource"].update(valueQuantity={"value": "abc"}), "numerico"),
        (lambda b: b["entry"].__delitem__(slice(1, None)), "al menos una Observation"),
    ],
)
def test_import_validates_strictly_and_writes_nothing(db, client_as, mutate, fragment) -> None:
    patients, observations = (
        db.scalar(select(func.count()).select_from(Patient)),
        db.scalar(select(func.count()).select_from(Observation)),
    )
    response = client_as("operador.norte").post(IMPORT, json=_broken(mutate))
    assert response.status_code == 422, response.text
    assert fragment in response.text
    assert db.scalar(select(func.count()).select_from(Patient)) == patients
    assert db.scalar(select(func.count()).select_from(Observation)) == observations
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.FHIR_IMPORT)) == 0


def test_a_non_object_body_is_rejected(client_as) -> None:
    assert client_as("admin").post(IMPORT, json=[1, 2]).status_code == 422


def test_import_accepts_the_fhir_json_content_type(client_as) -> None:
    import json

    response = client_as("operador.norte").post(
        IMPORT,
        content=json.dumps(_bundle(document="CC-9001003")),
        headers={"Content-Type": "application/fhir+json"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["paciente_creado"] is True
