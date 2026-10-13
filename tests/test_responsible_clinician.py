# ruff: noqa: F811
"""Responsible clinician: set on creation, changed under access rules, audited."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import AuditLog, Patient
from app.models.enums import AuditAction
from tests.test_ai_reports import CANARY, _create_patient
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


def _url(patient_id: str) -> str:
    return f"/api/v1/patients/{patient_id}/responsible-clinician"


def test_creator_clinician_becomes_responsible_and_name_is_exposed(db, client_as) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    operator = _user(db, "operador.norte")
    assert patient["responsible_clinician_id"] == str(operator.id)
    assert patient["responsible_clinician_name"] == operator.full_name
    again = client_as("admin").get(f"/api/v1/patients/{patient['id']}").json()
    assert again["responsible_clinician_name"] == operator.full_name


def test_an_admin_creating_a_patient_leaves_it_unassigned(db, client_as) -> None:
    eps = db.scalars(select(Patient)).first().eps_organization_id
    created = client_as("admin").post(
        "/api/v1/patients", json={**CANARY, "eps_organization_id": str(eps)}
    ).json()
    assert created["responsible_clinician_id"] is None
    assert created["responsible_clinician_name"] is None


def test_admin_and_colleague_can_change_it_and_it_is_audited(db, client_as) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    specialist = _user(db, "especialista.norte")

    by_colleague = client_as("operador.norte").put(_url(patient["id"]), json={"clinician_id": str(specialist.id)})
    assert by_colleague.status_code == 200, by_colleague.text
    assert by_colleague.json()["responsible_clinician_id"] == str(specialist.id)
    assert by_colleague.json()["responsible_clinician_name"] == specialist.full_name

    operator = _user(db, "operador.norte")
    by_admin = client_as("admin").put(_url(patient["id"]), json={"clinician_id": str(operator.id)})
    assert by_admin.status_code == 200 and by_admin.json()["responsible_clinician_id"] == str(operator.id)

    entries = list(db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.RESPONSIBLE_CHANGED)
                              .order_by(AuditLog.created_at)))
    assert len(entries) == 2
    assert entries[0].metadata_json["to"] == str(specialist.id)
    assert entries[0].metadata_json["from"] == str(operator.id)
    assert entries[0].entity_id == uuid.UUID(patient["id"])


def test_access_rules(db, client_as) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    sur = _user(db, "operador.sur")
    norte_specialist = _user(db, "especialista.norte")
    accountant = _user(db, "contable.eps")

    # A clinician of another IPS (no encounter of the patient there).
    assert client_as("operador.sur").put(_url(patient["id"]), json={"clinician_id": str(sur.id)}).status_code == 403
    # A clinician may only hand the patient to a colleague of his own IPS.
    assert client_as("operador.norte").put(_url(patient["id"]), json={"clinician_id": str(sur.id)}).status_code == 403
    for username in ("paciente.demo", "contable.eps", "coordinador.eps"):
        assert client_as(username).put(
            _url(patient["id"]), json={"clinician_id": str(norte_specialist.id)}
        ).status_code == 403
    # The responsible must be a clinician with an active account.
    assert client_as("admin").put(_url(patient["id"]), json={"clinician_id": str(accountant.id)}).status_code == 422
    assert client_as("admin").put(_url(patient["id"]), json={"clinician_id": str(uuid.uuid4())}).status_code == 404
    # Admin may pick a clinician of any IPS.
    assert client_as("admin").put(_url(patient["id"]), json={"clinician_id": str(sur.id)}).status_code == 200


def _eligible(client, patient_id: str):  # noqa: ANN001, ANN202
    return client.get(f"/api/v1/patients/{patient_id}/eligible-clinicians")


def test_eligible_clinicians_lists_the_ips_clinicians(db, client_as) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    operator = _user(db, "operador.norte")
    specialist = _user(db, "especialista.norte")

    for username in ("admin", "operador.norte", "especialista.norte"):
        response = _eligible(client_as(username), patient["id"])
        assert response.status_code == 200, (username, response.text)
        body = response.json()
        assert {c["id"] for c in body} == {str(operator.id), str(specialist.id)}
        by_id = {c["id"]: c for c in body}
        assert by_id[str(specialist.id)] == {
            "id": str(specialist.id), "nombre": specialist.full_name,
            "rol": "especialista", "organizacion": operator.organization.name,
        }
        assert by_id[str(operator.id)]["rol"] == "medico"

    # Every candidate is accepted by the endpoint that sets the responsible.
    for candidate in body:
        assert client_as("admin").put(
            _url(patient["id"]), json={"clinician_id": candidate["id"]}
        ).status_code == 200


def test_eligible_clinicians_skips_inactive_and_locked_accounts(db, client_as) -> None:
    from datetime import datetime, timezone

    patient = _create_patient(db, client_as, with_vitals=False)
    specialist = _user(db, "especialista.norte")
    specialist.is_active = False
    db.commit()
    assert {c["id"] for c in _eligible(client_as("admin"), patient["id"]).json()} == {
        str(_user(db, "operador.norte").id)
    }
    specialist.is_active = True
    specialist.locked_at = datetime.now(timezone.utc)
    db.commit()
    assert str(specialist.id) not in {c["id"] for c in _eligible(client_as("admin"), patient["id"]).json()}


def test_eligible_clinicians_access_rules(db, client_as) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    # A clinician of another IPS, and the other roles, are refused.
    for username in ("operador.sur", "paciente.demo", "contable.eps", "coordinador.eps"):
        assert _eligible(client_as(username), patient["id"]).status_code == 403, username
    assert _eligible(client_as("admin"), str(uuid.uuid4())).status_code == 404
    # A patient with no encounter anywhere has nobody to propose.
    eps = db.scalars(select(Patient)).first().eps_organization_id
    created = client_as("admin").post(
        "/api/v1/patients",
        json={**CANARY, "document_number": "5544332211", "eps_organization_id": str(eps)},
    ).json()
    assert _eligible(client_as("admin"), created["id"]).json() == []
