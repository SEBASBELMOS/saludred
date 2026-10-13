# ruff: noqa: F811
"""WebSocket live channel (R17): same registry and JSON as the SSE stream.

Driven with Starlette's ``TestClient.websocket_connect``. The app runs in the
client's portal thread, so ``registry.push`` called from the test thread goes
through ``call_soon_threadsafe`` exactly as a threadpool route would.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import events
from app.api.routes import events as events_route
from app.core.database import get_db
from app.core.security import create_access_token
from app.events.channel import registry
from app.main import app
from app.models import Patient
from tests.test_bed_rules import _user, db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_registry():  # noqa: ANN202
    registry.clear()
    yield
    registry.clear()


@pytest.fixture
def client(db):  # noqa: ANN001, ANN202, F811
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _token(user) -> str:  # noqa: ANN001
    return create_access_token(user.id, user.role.code.value)


def _ws_url(token: str | None) -> str:
    return "/api/v1/events/ws" if token is None else f"/api/v1/events/ws?token={token}"


def _wait_for_subscription(user_id: uuid.UUID, expected: int = 1) -> None:
    """The handshake returns before the server subscribes; wait for the registry."""

    deadline = time.monotonic() + 3
    while registry.connection_count(user_id) != expected:
        assert time.monotonic() < deadline, "the socket never subscribed"
        time.sleep(0.01)


def _expect_refused(client: TestClient, token: str | None) -> int:
    with client.websocket_connect(_ws_url(token)) as ws:
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_text()
    assert registry.connection_count() == 0
    return info.value.code


# -- authentication ---------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "not-a-jwt"])
def test_ws_refuses_a_missing_or_invalid_token(client, token) -> None:
    assert _expect_refused(client, token) == events_route.WS_UNAUTHORIZED == 4401


def test_ws_refuses_a_locked_account(client, db) -> None:
    user = _user(db, "operador.sur")
    token = _token(user)
    user.locked_at = datetime.now(timezone.utc)
    db.commit()
    assert _expect_refused(client, token) == 4401


def test_ws_refuses_a_disabled_account(client, db) -> None:
    user = _user(db, "especialista.norte")
    token = _token(user)
    user.is_active = False
    db.commit()
    assert _expect_refused(client, token) == 4401


# -- delivery and scope -----------------------------------------------------


def test_ws_delivers_the_event_json_to_the_addressed_user_only(client, db) -> None:
    doctor, other = _user(db, "operador.norte"), _user(db, "especialista.norte")
    patient_id = db.scalar(select(Patient.id).limit(1))
    with (
        client.websocket_connect(_ws_url(_token(doctor))) as ws_doctor,
        client.websocket_connect(_ws_url(_token(other))) as ws_other,
    ):
        _wait_for_subscription(doctor.id)
        _wait_for_subscription(other.id)
        events.publish(
            events.DomainEvent(
                tipo="fhir_observacion",
                mensaje="Nuevo resultado de laboratorio desde FHIR: Glucosa 182 mg/dL",
                recipients=(doctor.id,),
                paciente_id=patient_id,
            ),
            db,
        )
        payload = ws_doctor.receive_json()
        assert payload["tipo"] == "fhir_observacion"
        assert payload["mensaje"].startswith("Nuevo resultado")
        assert payload["paciente_id"] == str(patient_id)
        assert payload["fecha"] and payload["id"] and payload["leida"] is False

        # The other user's socket gets nothing: the next frame it sees is the
        # marker event sent to it afterwards.
        registry.push(other.id, {"tipo": "marcador", "mensaje": "m"})
        assert ws_other.receive_json()["tipo"] == "marcador"


@pytest.mark.parametrize("username", ["paciente.demo", "contable.eps"])
def test_ws_never_delivers_clinical_events_to_patient_or_accountant(
    client, db, username
) -> None:
    user = _user(db, username)
    with client.websocket_connect(_ws_url(_token(user))) as ws:
        _wait_for_subscription(user.id)
        # Even if a publisher lists the account, nothing clinical goes through...
        assert registry.push(user.id, {"tipo": "fhir_observacion"}, clinical=True) == 0
        events.publish(
            events.DomainEvent(tipo="fhir_observacion", mensaje="m", recipients=(user.id,)),
            db,
        )
        # ...while a non-clinical event proves the socket is alive and is first.
        assert registry.push(user.id, {"tipo": "factura", "mensaje": "ok"}, clinical=False) == 1
        assert ws.receive_json()["tipo"] == "factura"


def test_ws_every_tab_of_the_same_user_receives_it(client, db) -> None:
    user = _user(db, "operador.norte")
    token = _token(user)
    with client.websocket_connect(_ws_url(token)) as a, client.websocket_connect(_ws_url(token)) as b:
        _wait_for_subscription(user.id, expected=2)
        registry.push(user.id, {"tipo": "x", "mensaje": "dos pestañas"})
        assert a.receive_json()["mensaje"] == b.receive_json()["mensaje"] == "dos pestañas"


# -- heartbeat and cleanup --------------------------------------------------


def test_ws_sends_an_application_ping_when_idle(client, db, monkeypatch) -> None:
    monkeypatch.setattr(events_route, "HEARTBEAT_SECONDS", 0.05)
    user = _user(db, "operador.norte")
    with client.websocket_connect(_ws_url(_token(user))) as ws:
        assert ws.receive_json() == {"tipo": "ping"}
        assert ws.receive_json() == {"tipo": "ping"}


def test_ws_unsubscribes_when_the_client_disconnects(client, db) -> None:
    user = _user(db, "operador.norte")
    with client.websocket_connect(_ws_url(_token(user))):
        _wait_for_subscription(user.id)
    deadline = time.monotonic() + 3
    while registry.connection_count(user.id) != 0:
        assert time.monotonic() < deadline, "the connection was not cleaned up"
        time.sleep(0.01)


def test_ws_ignores_client_messages(client, db) -> None:
    user = _user(db, "operador.norte")
    with client.websocket_connect(_ws_url(_token(user))) as ws:
        _wait_for_subscription(user.id)
        ws.send_text("pong")
        registry.push(user.id, {"tipo": "x", "mensaje": "sigue vivo"})
        assert ws.receive_json()["mensaje"] == "sigue vivo"
