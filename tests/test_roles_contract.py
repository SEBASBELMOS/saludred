"""Six roles, their contract names and what each one may (not) reach.

Runs through the real app on the seeded dataset in SQLite, with the database
and the authenticated account swapped in (see ``client_as``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Organization, Patient, User
from app.models.enums import AuditAction, OrganizationType
from tests.test_bed_rules import client_as, db  # noqa: F401  (fixture reuse)

ACCOUNTS = {
    "admin": "admin",
    "operador.norte": "medico",
    "especialista.norte": "especialista",
    "paciente.demo": "paciente",
    "contable.eps": "contable",
    "coordinador.eps": "coordinador",
}


def _ips(db: Session, code: str) -> Organization:
    return db.scalar(select(Organization).where(Organization.code == code))


def _patient(db: Session) -> Patient:
    return db.scalars(select(Patient).where(Patient.deleted_at.is_(None))).first()


@pytest.mark.parametrize(("username", "rol"), list(ACCOUNTS.items()))
def test_me_exposes_the_contract_role_name(db, client_as, username, rol) -> None:  # noqa: F811
    response = client_as(username).get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["rol"] == rol


def test_me_keeps_the_original_role_field(db, client_as) -> None:  # noqa: F811
    body = client_as("especialista.norte").get("/api/v1/auth/me").json()
    assert body["role"] == "SPECIALIST"
    assert body["organization_id"] == str(_ips(db, "IPS-NORTE").id)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/patients?document_number=10000",
        "/api/v1/encounters",
        "/api/v1/observations",
        "/api/v1/imaging-studies",
        "/api/v1/bed-requests/queue",
        "/api/v1/network/capacity",
        "/api/v1/analytics/wait-times",
        "/api/v1/audit-logs",
        "/api/v1/organizations",
        "/api/v1/me/patient",
    ],
)
def test_accountant_cannot_reach_clinical_or_network_data(db, client_as, path) -> None:  # noqa: F811
    assert client_as("contable.eps").get(path).status_code == 403


def test_accountant_cannot_read_a_patient_detail(db, client_as) -> None:  # noqa: F811
    patient = _patient(db)
    assert client_as("contable.eps").get(f"/api/v1/patients/{patient.id}").status_code == 403


def test_specialist_reads_patients(db, client_as) -> None:  # noqa: F811
    client = client_as("especialista.norte")
    patient = _patient(db)
    assert client.get(f"/api/v1/patients/{patient.id}").status_code == 200
    search = client.get(f"/api/v1/patients?document_number={patient.document_number}")
    assert search.status_code == 200, search.text
    assert search.json()["total"] >= 1


def _encounter_payload(db: Session, organization: Organization) -> dict:
    return {
        "patient_id": str(_patient(db).id),
        "organization_id": str(organization.id),
        "encounter_class": "AMB",
        "status": "in-progress",
        "priority": "ROUTINE",
        "reason_text": "Control por especialista",
        "started_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc).isoformat(),
    }


def test_specialist_creates_encounter_and_observation_in_own_ips(db, client_as) -> None:  # noqa: F811
    client = client_as("especialista.norte")
    encounter = client.post(
        "/api/v1/encounters", json=_encounter_payload(db, _ips(db, "IPS-NORTE"))
    )
    assert encounter.status_code == 201, encounter.text
    data = encounter.json()

    observation = client.post(
        "/api/v1/observations",
        json={
            "patient_id": data["patient_id"],
            "encounter_id": data["id"],
            "code": "8867-4",
            "display": "Heart rate",
            "value_numeric": 80,
            "unit": "/min",
            "observed_at": datetime(2026, 9, 29, 8, 30, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert observation.status_code == 201, observation.text


def test_specialist_cannot_create_an_encounter_in_another_ips(db, client_as) -> None:  # noqa: F811
    other = db.scalars(
        select(Organization).where(
            Organization.organization_type == OrganizationType.IPS,
            Organization.code != "IPS-NORTE",
        )
    ).first()
    response = client_as("especialista.norte").post(
        "/api/v1/encounters", json=_encounter_payload(db, other)
    )
    assert response.status_code == 403


def test_specialist_cannot_create_a_patient(db, client_as) -> None:  # noqa: F811
    payload = {
        "document_type": "CC",
        "document_number": "99887766",
        "first_name": "Ana",
        "last_name": "Prueba",
        "birth_date": "1990-01-01",
        "gender": "female",
        "eps_organization_id": str(_patient(db).eps_organization_id),
    }
    response = client_as("especialista.norte").post("/api/v1/patients", json=payload)
    assert response.status_code == 403


def test_specialist_cannot_create_a_bed_request(db, client_as) -> None:  # noqa: F811
    payload = {
        "encounter_id": str(uuid.uuid4()),
        "required_service": "Hospitalizacion",
        "priority": "URGENT",
    }
    response = client_as("especialista.norte").post("/api/v1/bed-requests", json=payload)
    assert response.status_code == 403


def test_specialist_cannot_read_audit_logs_or_analytics(db, client_as) -> None:  # noqa: F811
    client = client_as("especialista.norte")
    assert client.get("/api/v1/audit-logs").status_code == 403
    assert client.get("/api/v1/analytics/wait-times").status_code == 403


def test_the_operator_cannot_read_audit_logs(db, client_as) -> None:  # noqa: F811
    assert client_as("operador.norte").get("/api/v1/audit-logs").status_code == 403


# -- Account creation ------------------------------------------------------


def _account(username: str, role: str, **extra) -> dict:
    return {"username": username, "full_name": "Persona de prueba", "role": role,
            "password": "Clave-de-prueba-1", **extra}


def test_admin_creates_a_specialist_with_an_ips(db, client_as) -> None:  # noqa: F811
    response = client_as("admin").post(
        "/api/v1/admin/users",
        json=_account("esp.nuevo", "SPECIALIST", organization_id=str(_ips(db, "IPS-NORTE").id)),
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "SPECIALIST"


def test_admin_creates_an_accountant_with_an_eps(db, client_as) -> None:  # noqa: F811
    eps = db.scalar(select(Organization).where(Organization.organization_type == OrganizationType.EPS))
    response = client_as("admin").post(
        "/api/v1/admin/users",
        json=_account("cont.nuevo", "ACCOUNTANT", organization_id=str(eps.id)),
    )
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "ACCOUNTANT"


def test_specialist_needs_an_ips_and_accountant_needs_an_eps(db, client_as) -> None:  # noqa: F811
    client = client_as("admin")
    eps = db.scalar(select(Organization).where(Organization.organization_type == OrganizationType.EPS))
    ips = _ips(db, "IPS-NORTE")
    cases = [
        _account("esp.sin.org", "SPECIALIST"),
        _account("esp.con.eps", "SPECIALIST", organization_id=str(eps.id)),
        _account("cont.sin.org", "ACCOUNTANT"),
        _account("cont.con.ips", "ACCOUNTANT", organization_id=str(ips.id)),
    ]
    for payload in cases:
        response = client.post("/api/v1/admin/users", json=payload)
        assert response.status_code == 422, (payload["username"], response.text)
    assert db.scalar(select(User).where(User.username == "esp.sin.org")) is None


# -- Audit actions ---------------------------------------------------------

NEW_ACTIONS = [
    "AI_ANALYSIS", "REPORT_APPROVED", "REPORT_REJECTED", "REFERRAL_CREATED",
    "REFERRAL_ACCEPTED", "INVOICE_CREATED", "ALERT_CREATED", "ALERT_ACKNOWLEDGED",
    "ALERT_ESCALATED", "FHIR_EVENT_RECEIVED", "CLEANING_EVIDENCE", "RECORD_VIEWED",
]


def test_audit_action_has_the_new_values() -> None:
    assert {a.value for a in AuditAction} >= set(NEW_ACTIONS)


def test_the_audit_check_accepts_a_new_action(db: Session) -> None:  # noqa: F811
    admin = db.scalar(select(User).where(User.username == "admin"))
    db.add(AuditLog(user_id=admin.id, action=AuditAction.AI_ANALYSIS, entity_type="patients"))
    db.commit()
    stored = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.AI_ANALYSIS))
    assert stored is not None
