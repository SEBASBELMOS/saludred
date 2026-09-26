"""Search instead of listing, catalogue-checked measurements, account creation.

These follow the week-8 course notebook: patients are found by search, never
browsed; the server owns the name, unit and plausible range of each vital
sign; and only the administration creates accounts.
"""

from __future__ import annotations

import contextlib
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.seed as seed_module
from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import AuditLog, Base, Encounter, Organization, Patient, User
from app.models.enums import AuditAction, OrganizationType


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk(connection, _record) -> None:  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            seed_module.seed(reset=True, patient_count=40)
    finally:
        seed_module.SessionLocal = original
    with factory() as session:
        yield session


@pytest.fixture
def client_as(db: Session):
    def build(username: str | None) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db
        if username:
            account = db.scalar(select(User).where(User.username == username))
            app.dependency_overrides[get_current_user] = lambda: account
        else:
            app.dependency_overrides.pop(get_current_user, None)
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Search, never listing
# ---------------------------------------------------------------------------


def test_patients_are_not_listed_without_a_search(client_as) -> None:
    response = client_as("admin").get("/api/v1/patients")
    assert response.status_code == 422
    assert "criterio" in response.json()["detail"]


@pytest.mark.parametrize("query", ["name=ra", "document_number=12345"])
def test_too_short_searches_are_refused(client_as, query: str) -> None:
    assert client_as("admin").get(f"/api/v1/patients?{query}").status_code == 422


def test_a_search_returns_at_most_ten_even_if_more_are_asked(db: Session, client_as) -> None:
    # Twelve patients sharing a surname, so the cap is actually exercised.
    template = db.scalar(select(Patient).limit(1))
    for n in range(12):
        db.add(Patient(
            document_type=template.document_type, document_number=f"77700{n:03d}",
            first_name="Prueba", last_name="Topeapellido", birth_date=template.birth_date,
            gender=template.gender, eps_organization_id=template.eps_organization_id,
        ))
    db.commit()

    response = client_as("admin").get("/api/v1/patients?name=topeapellido&page_size=100")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 12
    assert len(body["items"]) == 10
    assert body["page_size"] == 10


def test_an_exact_document_finds_one_patient(client_as) -> None:
    response = client_as("operador.norte").get("/api/v1/patients?document_number=1032450000")
    assert response.status_code == 200
    assert response.json()["total"] == 1


# ---------------------------------------------------------------------------
# Measurements checked against the catalogue
# ---------------------------------------------------------------------------


def _encounter(db: Session, username: str) -> Encounter:
    operator = db.scalar(select(User).where(User.username == username))
    return db.scalar(select(Encounter).where(Encounter.organization_id == operator.organization_id).limit(1))


def _observation(encounter: Encounter, code: str, value: float, unit: str = "x", display: str = "x") -> dict:
    return {
        "patient_id": str(encounter.patient_id),
        "encounter_id": str(encounter.id),
        "code": code,
        "display": display,
        "value_numeric": value,
        "unit": unit,
        "observed_at": "2026-09-26T10:00:00Z",
    }


def test_an_implausible_vital_sign_is_rejected(db: Session, client_as) -> None:
    """A heart rate of 4000 is not a very sick patient, it is a typo."""

    encounter = _encounter(db, "operador.norte")
    response = client_as("operador.norte").post(
        "/api/v1/observations", json=_observation(encounter, "8867-4", 4000, "/min", "Heart rate")
    )
    assert response.status_code == 422
    assert "rango plausible" in response.json()["detail"]


def test_the_server_sets_name_and_unit_from_the_catalogue(db: Session, client_as) -> None:
    encounter = _encounter(db, "operador.norte")
    response = client_as("operador.norte").post(
        "/api/v1/observations", json=_observation(encounter, "8867-4", 88, "latidos", "pulso")
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["display"] == "Heart rate"
    assert body["unit"] == "/min"


def test_codes_outside_the_catalogue_are_stored_as_sent(db: Session, client_as) -> None:
    encounter = _encounter(db, "operador.norte")
    response = client_as("operador.norte").post(
        "/api/v1/observations", json=_observation(encounter, "1234-5", 999, "mg", "Otro analito")
    )
    assert response.status_code == 201
    assert response.json()["unit"] == "mg"


# ---------------------------------------------------------------------------
# Accounts created by the administration
# ---------------------------------------------------------------------------


def _ips(db: Session) -> Organization:
    return db.scalar(select(Organization).where(Organization.organization_type == OrganizationType.IPS).limit(1))


def test_the_administration_creates_an_operator_who_can_log_in(db: Session, client_as) -> None:
    ips = _ips(db)
    response = client_as("admin").post(
        "/api/v1/admin/users",
        json={"username": "operador.nuevo", "full_name": "Operador Nuevo", "role": "IPS_CLINICAL_OPERATOR",
              "organization_id": str(ips.id), "password": "ClaveSegura2026"},
    )
    assert response.status_code == 201, response.text
    login = client_as(None).post("/api/v1/auth/login", json={"username": "operador.nuevo", "password": "ClaveSegura2026"})
    assert login.status_code == 200


def test_a_patient_account_is_linked_to_the_record(db: Session, client_as) -> None:
    patient = db.scalar(select(Patient).where(Patient.document_number != "1032450000").limit(1))
    response = client_as("admin").post(
        "/api/v1/admin/users",
        json={"username": "paciente.nuevo", "role": "PATIENT", "patient_document": patient.document_number,
              "password": "ClaveSegura2026"},
    )
    assert response.status_code == 201, response.text
    created = db.scalar(select(User).where(User.username == "paciente.nuevo"))
    assert created.patient_id == patient.id
    assert created.full_name == patient.full_name

    again = client_as("admin").post(
        "/api/v1/admin/users",
        json={"username": "paciente.otro", "role": "PATIENT", "patient_document": patient.document_number,
              "password": "ClaveSegura2026"},
    )
    assert again.status_code == 409


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"username": "admin", "role": "ADMIN", "full_name": "X", "password": "ClaveSegura2026"}, 409),
        ({"username": "sin.ips", "role": "IPS_CLINICAL_OPERATOR", "full_name": "X", "password": "ClaveSegura2026"}, 422),
        ({"username": "clave.corta", "role": "ADMIN", "full_name": "X", "password": "corta"}, 422),
        ({"username": "Mayusculas", "role": "ADMIN", "full_name": "X", "password": "ClaveSegura2026"}, 422),
        ({"username": "sin.ficha", "role": "PATIENT", "patient_document": "000000", "password": "ClaveSegura2026"}, 422),
    ],
)
def test_invalid_accounts_are_refused(client_as, payload: dict, status: int) -> None:
    assert client_as("admin").post("/api/v1/admin/users", json=payload).status_code == status


def test_only_the_administration_creates_accounts(client_as) -> None:
    response = client_as("coordinador.eps").post(
        "/api/v1/admin/users",
        json={"username": "intruso", "role": "ADMIN", "full_name": "X", "password": "ClaveSegura2026"},
    )
    assert response.status_code == 403


def test_the_password_never_reaches_the_audit_log(db: Session, client_as) -> None:
    client_as("admin").post(
        "/api/v1/admin/users",
        json={"username": "auditada", "role": "ADMIN", "full_name": "Auditada", "password": "SecretoMuySecreto"},
    )
    entry = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.CREATE, AuditLog.entity_type == "users"))
    assert entry is not None
    assert "SecretoMuySecreto" not in str(entry.metadata_json)
