# ruff: noqa: F811
"""R21 -- the HAPI Subscription rest-hook receiver.

HAPI is faked: the tests POST/PUT the exact shape HAPI sends (an update of the
payload against ``{endpoint}/Observation/{id}``) and check what the system does.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, FhirInboundEvent, Notification, Observation, Patient
from app.models.enums import AuditAction, ObservationOrigin
from app.services import fhir_inbound, ml_client  # noqa: F401
from tests.fhir_helpers import (
    HOOK,
    FakeFhir,
    glucose,
    hook_headers,
    open_encounter,
    sync_patient_to_fake,
)
from tests.test_ai_reports import CANARY, _create_patient  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

def _fhir_rows(db) -> int:  # noqa: ANN001
    return db.scalar(
        select(func.count()).select_from(Observation).where(Observation.origin == ObservationOrigin.FHIR)
    )


MESSAGE = "Nuevo resultado de laboratorio desde FHIR: Glucosa 182 mg/dL"


@pytest.fixture
def scene(db, client_as):
    """A patient created by operador.norte (so he is the responsible clinician),
    with an open encounter, already present in the fake HAPI."""

    patient = _create_patient(db, client_as, with_vitals=False)
    fake = FakeFhir()
    fhir_id = sync_patient_to_fake(db, fake, uuid.UUID(patient["id"]), _user(db, "admin"))
    return db, client_as, patient, fhir_id


def _put(client, fhir_id: str, obs_id: str = "obs-100", **kw):
    resource = glucose(f"Patient/{fhir_id}", obs_id=obs_id, **kw)
    return client.put(f"{HOOK}/Observation/{obs_id}", json=resource, headers=hook_headers())


def _inbox(client_as, username: str) -> list[dict]:
    return client_as(username).get("/api/v1/notifications").json()


# -- authentication ---------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-secret-0123456789"}, {"Authorization": "Basic abc"},
     {"Authorization": "Bearer "}],
)
def test_hook_rejects_missing_or_wrong_secret(scene, headers) -> None:
    db, client_as, patient, fhir_id = scene
    client = client_as("admin")
    resource = glucose(f"Patient/{fhir_id}")
    assert client.put(f"{HOOK}/Observation/obs-100", json=resource, headers=headers).status_code == 401
    assert client.post(f"{HOOK}/observation", json=resource, headers=headers).status_code == 401
    assert db.scalar(select(func.count()).select_from(FhirInboundEvent)) == 0


def test_a_user_jwt_is_not_a_hook_credential(scene) -> None:
    from app.core.security import create_access_token

    db, client_as, patient, fhir_id = scene
    token = create_access_token(_user(db, "admin").id, "ADMIN")
    response = client_as("admin").put(
        f"{HOOK}/Observation/obs-100",
        json=glucose(f"Patient/{fhir_id}"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


# -- shapes -----------------------------------------------------------------


def test_put_shape_used_by_hapi(scene) -> None:
    db, client_as, patient, fhir_id = scene
    response = _put(client_as("admin"), fhir_id)
    assert response.status_code == 200, response.text
    assert response.json()["resourceType"] == "OperationOutcome"
    assert response.headers["content-type"].startswith("application/fhir+json")


@pytest.mark.parametrize("path", ["/observation", "/Observation"])
def test_post_shapes(scene, path) -> None:
    db, client_as, patient, fhir_id = scene
    response = client_as("admin").post(
        f"{HOOK}{path}", json=glucose(f"Patient/{fhir_id}"), headers=hook_headers()
    )
    assert response.status_code == 200, response.text
    assert len(_inbox(client_as, "operador.norte")) == 1


def test_empty_body_and_delete_are_harmless(scene) -> None:
    _db, client_as, _patient, _fhir_id = scene
    client = client_as("admin")
    assert client.post(f"{HOOK}/observation", headers=hook_headers()).status_code == 200
    assert client.delete(f"{HOOK}/Observation/obs-1", headers=hook_headers()).status_code == 200
    bad = client.post(f"{HOOK}/observation", content=b"{not json", headers=hook_headers())
    assert bad.status_code == 422
    wrong = client.post(f"{HOOK}/observation", json={"resourceType": "Patient"}, headers=hook_headers())
    assert wrong.status_code == 422


# -- the full reaction ------------------------------------------------------


def test_observation_is_stored_with_origin_fhir_and_notifies_the_responsible(scene) -> None:
    db, client_as, patient, fhir_id = scene
    assert _put(client_as("admin"), fhir_id).status_code == 200

    row = db.scalar(select(Observation).where(Observation.fhir_resource_id == "obs-100"))
    assert row is not None
    assert row.origin == ObservationOrigin.FHIR
    assert (row.code, row.code_system, float(row.value_numeric), row.unit) == (
        "2339-0", "http://loinc.org", 182.0, "mg/dL",
    )
    assert str(row.patient_id) == patient["id"]
    assert str(row.encounter_id) == patient["encounter_id"]  # the open encounter
    assert row.created_by is None

    inbox = _inbox(client_as, "operador.norte")
    assert len(inbox) == 1
    note = inbox[0]
    assert set(note) >= {"id", "tipo", "mensaje", "leida", "fecha", "paciente_id"}
    assert note["tipo"] == "fhir_observacion"
    assert note["mensaje"] == MESSAGE
    assert note["leida"] is False
    assert note["paciente_id"] == patient["id"]
    assert note["datos"]["origen"] == "FHIR"

    # The observation shows in the patient's record, marked with its origin.
    listed = client_as("operador.norte").get(f"/api/v1/observations?patient_id={patient['id']}").json()
    assert [o["origin"] for o in listed["items"]] == ["fhir"]


def test_paciente_and_contable_never_receive_it(scene) -> None:
    db, client_as, patient, fhir_id = scene
    _put(client_as("admin"), fhir_id)
    for username in ("paciente.demo", "contable.eps", "coordinador.eps", "operador.sur"):
        assert _inbox(client_as, username) == [], username
    assert db.scalar(select(func.count()).select_from(Notification)) == 1


def test_audit_row_with_origin_fhir(scene) -> None:
    db, client_as, patient, fhir_id = scene
    _put(client_as("admin"), fhir_id)
    entry = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.FHIR_EVENT_RECEIVED))
    assert entry is not None and entry.user_id is None
    assert entry.entity_id == uuid.UUID(patient["id"])
    meta = entry.metadata_json
    assert meta["origin"] == "FHIR" and meta["patient_id"] == patient["id"]
    assert meta["fhir_resource"] == "Observation/obs-100" and meta["stored"] is True
    # Readable through the admin endpoint too.
    page = client_as("admin").get("/api/v1/audit-logs?action=FHIR_EVENT_RECEIVED").json()
    assert page["total"] == 1


# -- idempotency, echo, unknown patient ------------------------------------


def test_same_id_and_version_twice_is_processed_once(scene) -> None:
    db, client_as, patient, fhir_id = scene
    client = client_as("admin")
    assert _put(client, fhir_id).status_code == 200
    assert _put(client, fhir_id).status_code == 200  # HAPI retried
    assert db.scalar(select(func.count()).select_from(Observation).where(
        Observation.fhir_resource_id == "obs-100")) == 1
    assert db.scalar(select(func.count()).select_from(FhirInboundEvent)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.FHIR_EVENT_RECEIVED)) == 1
    assert len(_inbox(client_as, "operador.norte")) == 1


def test_a_new_version_is_a_new_event(scene) -> None:
    db, client_as, patient, fhir_id = scene
    client = client_as("admin")
    _put(client, fhir_id, version="1")
    _put(client, fhir_id, version="2", value=190)
    assert len(_inbox(client_as, "operador.norte")) == 2


def test_unversioned_deliveries_are_deduplicated_by_content(scene) -> None:
    db, client_as, patient, fhir_id = scene
    client = client_as("admin")
    _put(client, fhir_id, version=None)
    _put(client, fhir_id, version=None)
    _put(client, fhir_id, version=None, value=200)
    assert len(_inbox(client_as, "operador.norte")) == 2


def test_echo_of_our_own_push_is_ignored(scene) -> None:
    db, client_as, patient, fhir_id = scene
    tag = {"system": "urn:saludred:identifier:origin", "code": "saludred"}
    response = _put(client_as("admin"), fhir_id, tags=[tag])
    assert response.status_code == 200
    assert _fhir_rows(db) == 0
    assert db.scalar(select(func.count()).select_from(FhirInboundEvent)) == 0
    assert _inbox(client_as, "operador.norte") == []


def test_unknown_patient_is_audited_and_creates_nothing(scene) -> None:
    db, client_as, patient, _fhir_id = scene
    before = db.scalar(select(func.count()).select_from(Patient))
    response = _put(client_as("admin"), "no-such-patient")
    assert response.status_code == 200
    assert db.scalar(select(func.count()).select_from(Patient)) == before
    assert _fhir_rows(db) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == 0
    entry = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.FHIR_EVENT_RECEIVED))
    assert entry.metadata_json["origin"] == "FHIR"
    assert entry.metadata_json["outcome"] == "unknown_patient"
    event = db.scalar(select(FhirInboundEvent))
    assert event.outcome.value == "unknown_patient" and event.patient_id is None


def test_patient_resolved_by_project_identifier(scene) -> None:
    db, client_as, patient, _fhir_id = scene
    resource = glucose("Patient/ignored")
    resource["subject"] = {
        "identifier": {"system": "urn:saludred:identifier:documento",
                       "value": f"CC-{CANARY['document_number']}"}
    }
    response = client_as("admin").put(
        f"{HOOK}/Observation/obs-100", json=resource, headers=hook_headers()
    )
    assert response.status_code == 200
    assert len(_inbox(client_as, "operador.norte")) == 1


def _identifier_put(client, value: str, obs_id: str = "obs-ident"):
    resource = glucose("Patient/ignored", obs_id=obs_id)
    resource["subject"] = {"identifier": {"value": value}}  # no system: what a plain client sends
    return client.put(f"{HOOK}/Observation/{obs_id}", json=resource, headers=hook_headers())


@pytest.mark.parametrize("form", ["uuid", "number", "typed"])
def test_a_system_less_identifier_resolves_the_patient(scene, form) -> None:
    db, client_as, patient, _fhir_id = scene
    value = {
        "uuid": patient["id"],
        "number": CANARY["document_number"],
        "typed": f"CC-{CANARY['document_number']}",
    }[form]
    assert fhir_inbound.patient_by_identifier(db, None, value).id == uuid.UUID(patient["id"])
    assert _identifier_put(client_as("admin"), value).status_code == 200
    assert len(_inbox(client_as, "operador.norte")) == 1


def test_a_system_less_identifier_that_matches_two_patients_stays_unknown(scene) -> None:
    db, client_as, patient, _fhir_id = scene
    eps = db.scalar(select(Patient)).eps_organization_id
    twin = client_as("operador.norte").post(
        "/api/v1/patients",
        json={**CANARY, "document_type": "TI", "email": "gemelo@canario.test",
              "eps_organization_id": str(eps)},
    )
    assert twin.status_code == 201, twin.text
    number = CANARY["document_number"]
    assert fhir_inbound.patient_by_identifier(db, None, number) is None  # CC and TI share it
    assert fhir_inbound.patient_by_identifier(db, None, f"CC-{number}").id == uuid.UUID(patient["id"])

    assert _identifier_put(client_as("admin"), number).status_code == 200
    assert _inbox(client_as, "operador.norte") == []
    entry = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.FHIR_EVENT_RECEIVED))
    assert entry.metadata_json["outcome"] == "unknown_patient"


@pytest.mark.parametrize("form", ["uuid", "number", "typed"])
def test_a_deleted_patient_does_not_resolve_by_a_system_less_identifier(scene, form) -> None:
    db, client_as, patient, _fhir_id = scene
    value = {
        "uuid": patient["id"],
        "number": CANARY["document_number"],
        "typed": f"CC-{CANARY['document_number']}",
    }[form]
    row = db.get(Patient, uuid.UUID(patient["id"]))
    row.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert fhir_inbound.patient_by_identifier(db, None, value) is None
    assert fhir_inbound.patient_by_identifier(db, "urn:other:system", value) is None  # unchanged


# -- recipients -------------------------------------------------------------


def test_fallback_goes_to_the_clinicians_of_the_latest_encounter_ips(db, client_as) -> None:
    admin = _user(db, "admin")
    fake = FakeFhir()
    norte = _user(db, "operador.norte")
    # A fresh patient with only one encounter, at the norte IPS.
    patient = db.scalar(select(Patient).limit(1))
    patient.responsible_clinician_id = None
    for enc in list(patient.encounters):
        enc.deleted_at = enc.started_at
    db.commit()
    open_encounter(db, patient.id, norte.organization_id, admin)
    fhir_id = sync_patient_to_fake(db, fake, patient.id, admin)

    assert _put(client_as("admin"), fhir_id).status_code == 200
    assert len(_inbox(client_as, "operador.norte")) == 1
    assert len(_inbox(client_as, "especialista.norte")) == 1
    assert _inbox(client_as, "operador.sur") == []
    assert _inbox(client_as, "admin") == []


def test_without_encounters_the_admins_are_notified(db, client_as) -> None:
    admin = _user(db, "admin")
    patient = db.scalar(select(Patient).limit(1))
    patient.responsible_clinician_id = None
    for enc in list(patient.encounters):
        enc.deleted_at = enc.started_at
    db.commit()
    fhir_id = sync_patient_to_fake(db, FakeFhir(), patient.id, admin)
    assert _put(client_as("admin"), fhir_id).status_code == 200
    assert len(_inbox(client_as, "admin")) == 1
    assert _inbox(client_as, "operador.norte") == []


def test_a_locked_responsible_falls_back(scene) -> None:
    from datetime import datetime, timezone

    db, client_as, patient, fhir_id = scene
    _user(db, "operador.norte").locked_at = datetime.now(timezone.utc)
    db.commit()
    _put(client_as("admin"), fhir_id)
    # operador.norte is locked: his colleague of the same IPS is told instead.
    assert len(_inbox(client_as, "especialista.norte")) == 1


# -- no encounter -----------------------------------------------------------


def test_patient_without_encounter_is_notified_but_not_stored(db, client_as) -> None:
    admin = _user(db, "admin")
    patient = db.scalar(select(Patient).limit(1))
    patient.responsible_clinician_id = _user(db, "operador.norte").id
    for enc in list(patient.encounters):
        enc.deleted_at = enc.started_at
    db.commit()
    fhir_id = sync_patient_to_fake(db, FakeFhir(), patient.id, admin)
    assert _put(client_as("admin"), fhir_id).status_code == 200
    assert db.scalar(select(func.count()).select_from(Observation).where(
        Observation.fhir_resource_id == "obs-100")) == 0
    event = db.scalar(select(FhirInboundEvent))
    assert event.observation_id is None and event.resource["id"] == "obs-100"
    note = _inbox(client_as, "operador.norte")
    assert len(note) == 1 and note[0]["datos"]["almacenada"] is False
