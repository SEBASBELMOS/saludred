# ruff: noqa: F811
"""R15 -- automatic push to HAPI, retry, and the Subscription bootstrap."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models import Patient
from app.models.enums import SyncStatus
from app.services import fhir_autosync, fhir_subscription
from app.services.fhir_sync import get_fhir_client
from tests.fhir_helpers import FakeFhir, HOOK, glucose, hook_headers, sync_record
from tests.test_ai_reports import CANARY
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


@pytest.fixture
def hapi(db, monkeypatch):
    """Auto-sync ON, pointed at a fake HAPI, with its own sessions."""

    fake = FakeFhir()
    monkeypatch.setattr(get_settings(), "fhir_auto_sync", True)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    fhir_autosync.configure(session_factory=factory, client_factory=lambda: fake)
    yield fake
    fhir_autosync.configure()


def _new_patient(client_as, db, **overrides) -> dict:  # noqa: ANN001
    eps = db.scalars(select(Patient)).first().eps_organization_id
    response = client_as("operador.norte").post(
        "/api/v1/patients", json={**CANARY, "eps_organization_id": str(eps), **overrides}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_nothing_is_pushed_when_auto_sync_is_off(db, client_as) -> None:
    fake = FakeFhir()
    fhir_autosync.configure(client_factory=lambda: fake)
    try:
        _new_patient(client_as, db)
    finally:
        fhir_autosync.configure()
    assert fake.calls == []


def test_creating_a_patient_pushes_it_and_it_is_findable_by_identifier(db, client_as, hapi) -> None:
    patient = _new_patient(client_as, db)
    assert ("PUT", "Patient") in hapi.calls
    record = sync_record(db, uuid.UUID(patient["id"]))
    assert record.sync_status == SyncStatus.SYNCED and record.fhir_resource_id

    # R15: by the sync key, by the bare number and by the API's patient id.
    for value in (f"CC-{CANARY['document_number']}", CANARY["document_number"], patient["id"]):
        found = hapi.find_by_identifier("Patient", value)
        assert [r["id"] for r in found] == [record.fhir_resource_id], value
    # And every outbound resource carries the origin tag the hook uses for echo.
    tags = hapi.store["Patient"][record.fhir_resource_id]["meta"]["tag"]
    assert {"system": "urn:saludred:identifier:origin", "code": "saludred"} in [
        {k: t[k] for k in ("system", "code")} for t in tags
    ]


def test_editing_a_patient_pushes_again(db, client_as, hapi) -> None:
    patient = _new_patient(client_as, db)
    hapi.calls.clear()
    response = client_as("operador.norte").put(
        f"/api/v1/patients/{patient['id']}", json={"phone": "3000000000"}
    )
    assert response.status_code == 200
    assert hapi.calls == [("PUT", "Patient")]  # the EPS was already synced: not re-sent
    record = sync_record(db, uuid.UUID(patient["id"]))
    stored = hapi.store["Patient"][record.fhir_resource_id]
    assert {"system": "phone", "value": "3000000000"} in stored["telecom"]


def test_encounters_and_observations_are_pushed_and_echo_is_recognized(db, client_as, hapi) -> None:
    from tests.test_ai_reports import _create_patient

    patient = _create_patient(db, client_as, with_vitals=False)
    assert ("PUT", "Encounter") in hapi.calls
    client = client_as("operador.norte")
    response = client.post(
        "/api/v1/observations",
        json={
            "patient_id": patient["id"], "encounter_id": patient["encounter_id"],
            "code": "2339-0", "display": "Glucose", "value_numeric": 150, "unit": "mg/dL",
            "observed_at": datetime(2026, 10, 3, 9, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 201
    pushed = next(iter(hapi.store["Observation"].values()))

    # HAPI fires the Subscription for that very resource: it must be ignored.
    fhir_id = sync_record(db, uuid.UUID(patient["id"])).fhir_resource_id
    assert pushed["subject"]["reference"] == f"Patient/{fhir_id}"
    delivered = client.put(f"{HOOK}/Observation/{pushed['id']}", json=pushed, headers=hook_headers())
    assert delivered.status_code == 200
    assert client.get("/api/v1/notifications").json() == []


def test_a_hapi_outage_never_breaks_the_clinical_write(db, client_as, hapi) -> None:
    hapi.down = True
    patient = _new_patient(client_as, db)  # 201 asserted inside
    record = sync_record(db, uuid.UUID(patient["id"]))
    assert record is not None and record.sync_status == SyncStatus.ERROR
    assert "server down" in record.error_message

    # The patient is intact and can still be edited and read.
    assert client_as("operador.norte").put(
        f"/api/v1/patients/{patient['id']}", json={"phone": "3111111111"}
    ).status_code == 200
    assert client_as("operador.norte").get(f"/api/v1/patients/{patient['id']}").status_code == 200


def test_retry_failed_pushes_the_patients_that_never_made_it(db, client_as, hapi) -> None:
    hapi.down = True
    patient = _new_patient(client_as, db)
    pid = uuid.UUID(patient["id"])
    assert sync_record(db, pid).sync_status == SyncStatus.ERROR

    app_overrides = client_as("admin")
    from app.main import app

    app.dependency_overrides[get_fhir_client] = lambda: hapi
    # Still down: the batch reports failures instead of crashing.
    down = app_overrides.post("/api/v1/integration/fhir/retry-failed?limit=200")
    assert down.status_code == 200 and down.json()["fallidos"] >= 1
    assert down.json()["sincronizados"] == 0

    hapi.down = False
    done = app_overrides.post("/api/v1/integration/fhir/retry-failed?limit=200")
    body = done.json()
    assert done.status_code == 200 and body["fallidos"] == 0
    assert patient["id"] in body["ids_sincronizados"]
    db.expire_all()
    assert sync_record(db, pid).sync_status == SyncStatus.SYNCED

    # Nothing left to retry for that patient.
    again = app_overrides.post("/api/v1/integration/fhir/retry-failed?limit=200").json()
    assert patient["id"] not in again["ids_sincronizados"]


def test_retry_failed_is_admin_only(db, client_as) -> None:
    for username in ("operador.norte", "especialista.norte", "contable.eps", "paciente.demo"):
        assert client_as(username).post("/api/v1/integration/fhir/retry-failed").status_code == 403


def test_observations_from_fhir_are_never_pushed_back(db, client_as, hapi) -> None:
    from app.services import fhir_sync

    patient = _new_patient(client_as, db)
    # A FHIR-origin row, as the hook would store it.
    from tests.fhir_helpers import open_encounter

    admin = _user(db, "admin")
    encounter = open_encounter(db, uuid.UUID(patient["id"]), _user(db, "operador.norte").organization_id, admin)
    client_as("admin").post(
        f"{HOOK}/Observation", json=glucose(f"Patient/{sync_record(db, uuid.UUID(patient['id'])).fhir_resource_id}"),
        headers=hook_headers(),
    )
    from app.models import Observation

    row = db.scalar(select(Observation).where(Observation.fhir_resource_id == "obs-100"))
    assert row is not None and row.encounter_id == encounter.id
    from app.services.errors import ConflictError

    with pytest.raises(ConflictError):
        fhir_sync.sync_observation(db, hapi, row, actor=admin)


# -- Subscription bootstrap ---------------------------------------------------


def test_subscription_is_created_then_refreshed_not_duplicated() -> None:
    fake = FakeFhir()
    first = fhir_subscription.ensure_subscription(fake)
    second = fhir_subscription.ensure_subscription(fake)
    assert (first["accion"], second["accion"]) == ("creada", "actualizada")
    assert first["id"] == second["id"]
    assert len(fake.store["Subscription"]) == 1

    stored = next(iter(fake.store["Subscription"].values()))
    settings = get_settings()
    assert stored["status"] != "off"
    assert stored["criteria"] == "Observation?"
    assert stored["reason"]
    assert stored["channel"] == {
        "type": "rest-hook",
        "endpoint": settings.fhir_hook_endpoint,
        "payload": "application/fhir+json",
        "header": [f"Authorization: Bearer {settings.fhir_hook_secret}"],
    }
    assert settings.fhir_hook_endpoint == "http://api:8000/api/v1/fhir/hooks"
    assert "header" not in first and settings.fhir_hook_secret not in str(first)


def test_subscription_endpoint_is_admin_only_audited_and_hides_the_secret(db, client_as) -> None:
    from app.main import app
    from app.models import AuditLog
    from app.models.enums import AuditAction

    fake = FakeFhir()
    app.dependency_overrides[get_fhir_client] = lambda: fake
    assert client_as("operador.norte").post("/api/v1/integration/fhir/subscription").status_code == 403
    app.dependency_overrides[get_fhir_client] = lambda: fake
    response = client_as("admin").post("/api/v1/integration/fhir/subscription")
    assert response.status_code == 200, response.text
    assert response.json()["accion"] == "creada"
    assert get_settings().fhir_hook_secret not in response.text
    entry = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.FHIR_SYNC,
                                             AuditLog.entity_type == "fhir_subscriptions"))
    assert entry.metadata_json["origin"] == "FHIR"


def test_bootstrap_script_reports_failures_with_exit_code(monkeypatch, capsys) -> None:
    from app.services.errors import FhirGatewayError
    from scripts import ensure_fhir_subscription as script

    def boom(_client):  # noqa: ANN001, ANN202
        raise FhirGatewayError("test: down")

    monkeypatch.setattr(fhir_subscription, "ensure_subscription", boom)
    assert script.main() == 1
    monkeypatch.setattr(fhir_subscription, "ensure_subscription", lambda _c: {"accion": "creada"})
    assert script.main() == 0
    assert '"accion": "creada"' in capsys.readouterr().out
