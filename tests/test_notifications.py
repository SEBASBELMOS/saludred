# ruff: noqa: F811
"""R18 base -- the per-user notification inbox."""

from __future__ import annotations

import uuid

import pytest

from app import events
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

USERS = ["admin", "coordinador.eps", "operador.norte", "especialista.norte", "paciente.demo", "contable.eps"]


def _send(db, username: str, message: str, **kw) -> None:  # noqa: ANN001
    events.publish(
        events.DomainEvent(
            tipo="prueba", mensaje=message, recipients=(_user(db, username).id,),
            clinical=False, **kw
        ),
        db,
    )


@pytest.mark.parametrize("username", USERS)
def test_every_role_has_an_inbox(db, client_as, username) -> None:
    _send(db, username, f"para {username}")
    response = client_as(username).get("/api/v1/notifications")
    assert response.status_code == 200
    body = response.json()
    assert [n["mensaje"] for n in body] == [f"para {username}"]
    assert set(body[0]) >= {"id", "tipo", "mensaje", "leida", "fecha", "paciente_id"}
    assert body[0]["leida"] is False


def test_inbox_is_private_newest_first_and_filterable(db, client_as) -> None:
    _send(db, "operador.norte", "primera")
    _send(db, "operador.norte", "segunda")
    _send(db, "especialista.norte", "ajena")
    client = client_as("operador.norte")

    body = client.get("/api/v1/notifications").json()
    assert [n["mensaje"] for n in body] == ["segunda", "primera"]

    first = body[1]["id"]
    assert client.post(f"/api/v1/notifications/{first}/read").status_code == 200
    unread = client.get("/api/v1/notifications?leida=false").json()
    assert [n["mensaje"] for n in unread] == ["segunda"]
    read = client.get("/api/v1/notifications?leida=true").json()
    assert [n["mensaje"] for n in read] == ["primera"]


def test_mark_as_read_sets_leida_and_is_idempotent(db, client_as) -> None:
    _send(db, "operador.norte", "hola")
    client = client_as("operador.norte")
    note_id = client.get("/api/v1/notifications").json()[0]["id"]
    for _ in range(2):
        response = client.post(f"/api/v1/notifications/{note_id}/read")
        assert response.status_code == 200
        assert response.json()["leida"] is True
    assert client.get("/api/v1/notifications").json()[0]["leida"] is True


def test_only_the_owner_can_mark_as_read(db, client_as) -> None:
    _send(db, "operador.norte", "privada")
    note_id = client_as("operador.norte").get("/api/v1/notifications").json()[0]["id"]

    # Someone else's notification is a 404, like one that does not exist.
    for username in ("especialista.norte", "admin", "paciente.demo"):
        other = client_as(username)
        assert other.post(f"/api/v1/notifications/{note_id}/read").status_code == 404
        assert other.get("/api/v1/notifications").json() == []
    assert client_as("operador.norte").get("/api/v1/notifications").json()[0]["leida"] is False
    assert client_as("operador.norte").post(
        f"/api/v1/notifications/{uuid.uuid4()}/read"
    ).status_code == 404


def test_inbox_requires_authentication(db) -> None:
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        assert TestClient(app).get("/api/v1/notifications").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_inbox_hides_notifications_of_a_deleted_patient_but_keeps_the_rows(db, client_as) -> None:
    from sqlalchemy import select

    from app.models import Notification, Patient
    from app.services import notifications as notifications_service
    from tests.test_ai_reports import _create_patient

    patient = _create_patient(db, client_as, with_vitals=False)
    operator = _user(db, "operador.norte")
    _send(db, "operador.norte", "del paciente", paciente_id=uuid.UUID(patient["id"]))
    _send(db, "operador.norte", "sin paciente")
    client = client_as("operador.norte")

    shown = client.get("/api/v1/notifications").json()
    assert {n["mensaje"] for n in shown} == {"del paciente", "sin paciente"}
    assert notifications_service.count_unread(db, operator) == 2

    assert client.delete(f"/api/v1/patients/{patient['id']}").status_code == 204
    assert {n["mensaje"] for n in client.get("/api/v1/notifications").json()} == {"sin paciente"}
    assert [n["mensaje"] for n in client.get("/api/v1/notifications?leida=false").json()] == ["sin paciente"]
    assert notifications_service.count_unread(db, operator) == 1
    # History stays in the table.
    assert db.scalar(
        select(Notification).where(Notification.patient_id == uuid.UUID(patient["id"]))
    ) is not None

    # Restored: the notification comes back to the inbox.
    assert client_as("admin").post(f"/api/v1/patients/{patient['id']}/restore").status_code == 200
    assert {n["mensaje"] for n in client_as("operador.norte").get("/api/v1/notifications").json()} == {
        "del paciente", "sin paciente"
    }
    assert db.get(Patient, uuid.UUID(patient["id"])).deleted_at is None
