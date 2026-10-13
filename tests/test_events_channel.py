# ruff: noqa: F811
"""Event bus, live channel registry and the SSE endpoint (R17, R19).

The registry is tested directly (threads, roles, cleanup). No test opens a real
streaming HTTP connection: an infinite stream is exactly what makes a test
flaky. The generator that feeds the response is driven by hand instead.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import events
from app.api.routes import events as events_route
from app.core.security import create_access_token
from app.events import bus
from app.events.channel import registry
from app.models import Notification
from app.models.enums import RoleCode
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_registry():  # noqa: ANN202
    registry.clear()
    yield
    registry.clear()


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.new_event_loop().run_until_complete(coro)


# -- registry ---------------------------------------------------------------


def test_event_reaches_the_addressed_user_and_not_another() -> None:
    async def scenario() -> tuple:
        loop = asyncio.get_running_loop()
        alice, bob = uuid.uuid4(), uuid.uuid4()
        a = registry.subscribe(alice, RoleCode.IPS_CLINICAL_OPERATOR, loop)
        b = registry.subscribe(bob, RoleCode.IPS_CLINICAL_OPERATOR, loop)
        delivered = registry.push(alice, {"tipo": "x", "mensaje": "para alice"})
        await asyncio.sleep(0)
        return delivered, a.queue.qsize(), b.queue.qsize(), a.queue.get_nowait()

    delivered, alice_q, bob_q, payload = _run(scenario())
    assert (delivered, alice_q, bob_q) == (1, 1, 0)
    assert payload["mensaje"] == "para alice"


def test_every_tab_of_the_same_user_receives_it() -> None:
    async def scenario() -> list[int]:
        loop = asyncio.get_running_loop()
        user = uuid.uuid4()
        tabs = [registry.subscribe(user, RoleCode.SPECIALIST, loop) for _ in range(2)]
        registry.push(user, {"tipo": "x"})
        await asyncio.sleep(0)
        return [t.queue.qsize() for t in tabs]

    assert _run(scenario()) == [1, 1]


def test_push_from_a_worker_thread_uses_the_server_loop() -> None:
    """publish() runs in the threadpool; the queue belongs to the server loop."""

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        user = uuid.uuid4()
        conn = registry.subscribe(user, RoleCode.SPECIALIST, loop)
        pusher = threading.Thread(target=registry.push, args=(user, {"tipo": "desde-hilo"}))
        pusher.start()
        pusher.join()
        got = asyncio.run_coroutine_threadsafe(conn.queue.get(), loop).result(timeout=3)
        assert got["tipo"] == "desde-hilo"
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
        loop.close()


@pytest.mark.parametrize("role", [RoleCode.PATIENT, RoleCode.ACCOUNTANT])
def test_clinical_events_never_reach_patient_or_accountant_connections(role) -> None:
    async def scenario() -> tuple:
        loop = asyncio.get_running_loop()
        user = uuid.uuid4()
        conn = registry.subscribe(user, role, loop)
        clinical = registry.push(user, {"tipo": "fhir_observacion"}, clinical=True)
        other = registry.push(user, {"tipo": "factura"}, clinical=False)
        await asyncio.sleep(0)
        return clinical, other, conn.queue.qsize()

    assert _run(scenario()) == (0, 1, 1)


def test_unsubscribe_cleans_up_and_a_closed_loop_is_dropped() -> None:
    loop = asyncio.new_event_loop()
    user = uuid.uuid4()
    conn = registry.subscribe(user, RoleCode.SPECIALIST, loop)
    assert registry.connection_count(user) == 1
    loop.close()  # the server loop went away
    assert registry.push(user, {"tipo": "x"}) == 0
    assert registry.connection_count(user) == 0
    registry.unsubscribe(conn)  # idempotent


def test_a_slow_consumer_loses_the_oldest_event_not_the_stream() -> None:
    async def scenario() -> tuple:
        loop = asyncio.get_running_loop()
        user = uuid.uuid4()
        conn = registry.subscribe(user, RoleCode.SPECIALIST, loop)
        for i in range(conn.queue.maxsize + 5):
            registry.push(user, {"n": i})
        await asyncio.sleep(0)
        return conn.queue.qsize(), conn.queue.get_nowait()["n"]

    size, first = _run(scenario())
    assert size == 100 and first == 5


# -- generator behind the SSE response --------------------------------------


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def test_stream_generator_sends_events_heartbeats_and_cleans_up() -> None:
    async def scenario() -> tuple:
        identity = events_route.StreamIdentity(uuid.uuid4(), RoleCode.SPECIALIST)
        gen = events_route.event_stream(_Request(), identity, heartbeat_seconds=0.05)
        hello = await gen.__anext__()
        assert registry.connection_count(identity.user_id) == 1
        beat = await gen.__anext__()  # nothing queued: a heartbeat comment
        registry.push(identity.user_id, {"id": "n-1", "tipo": "fhir_observacion", "mensaje": "hola"})
        frame = await gen.__anext__()
        while frame.startswith(":"):  # a heartbeat may slip in before the event
            frame = await gen.__anext__()
        await gen.aclose()
        return hello, beat, frame, registry.connection_count(identity.user_id)

    hello, beat, frame, remaining = _run(scenario())
    assert "retry:" in hello
    assert beat == ": heartbeat\n\n"
    assert frame.startswith("id: n-1\nevent: notificacion\ndata: ")
    assert json.loads(frame.split("data: ", 1)[1])["mensaje"] == "hola"
    assert frame.endswith("\n\n")
    assert remaining == 0


def test_stream_generator_stops_when_the_client_disconnects() -> None:
    class Gone:
        async def is_disconnected(self) -> bool:
            return True

    async def scenario() -> tuple:
        identity = events_route.StreamIdentity(uuid.uuid4(), RoleCode.SPECIALIST)
        out = [chunk async for chunk in events_route.event_stream(Gone(), identity)]
        return out, registry.connection_count()

    out, remaining = _run(scenario())
    assert len(out) == 1 and remaining == 0


# -- SSE endpoint authentication -------------------------------------------


def test_stream_requires_a_token(client_as) -> None:
    client = client_as("admin")
    assert client.get("/api/v1/events/stream").status_code == 401
    assert client.get("/api/v1/events/stream?token=").status_code == 401
    assert client.get("/api/v1/events/stream?token=not-a-jwt").status_code == 401


def test_stream_refuses_locked_and_disabled_accounts(db, client_as) -> None:
    client = client_as("admin")
    locked = _user(db, "operador.sur")
    token = create_access_token(locked.id, "IPS_CLINICAL_OPERATOR")
    locked.locked_at = datetime.now(timezone.utc)
    db.commit()
    assert client.get(f"/api/v1/events/stream?token={token}").status_code == 401

    disabled = _user(db, "especialista.norte")
    token = create_access_token(disabled.id, "SPECIALIST")
    disabled.is_active = False
    db.commit()
    assert client.get(f"/api/v1/events/stream?token={token}").status_code == 401


def test_stream_identity_accepts_a_valid_token_and_releases_the_session(db) -> None:
    user = _user(db, "operador.norte")
    token = create_access_token(user.id, "IPS_CLINICAL_OPERATOR")
    identity = events_route.stream_identity(db, token)
    assert identity.user_id == user.id and identity.role == RoleCode.IPS_CLINICAL_OPERATOR


def test_stream_response_headers_disable_buffering() -> None:
    response = _run(events_route.stream(_Request(), events_route.StreamIdentity(uuid.uuid4(), RoleCode.ADMIN)))
    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"


# -- bus --------------------------------------------------------------------


def test_a_failing_reactor_is_isolated(db, caplog) -> None:
    calls: list[str] = []

    def broken(_event, _db) -> None:  # noqa: ANN001
        raise RuntimeError("boom")

    def healthy(event, _db) -> None:  # noqa: ANN001
        calls.append(event.tipo)

    bus.register_reactor(broken)
    bus.register_reactor(healthy)
    try:
        events.publish(events.DomainEvent(tipo="prueba", mensaje="m", recipients=()), db)
    finally:
        bus.unregister_reactor(broken)
        bus.unregister_reactor(healthy)
    assert calls == ["prueba"]
    assert "Reactor broken failed" in caplog.text


def test_registering_twice_is_a_noop() -> None:
    def reactor(_e, _d) -> None:  # noqa: ANN001
        pass

    before = len(bus.registered_reactors())
    bus.register_reactor(reactor)
    bus.register_reactor(reactor)
    try:
        assert len(bus.registered_reactors()) == before + 1
    finally:
        bus.unregister_reactor(reactor)


def test_notification_reactor_persists_and_pushes_only_to_allowed_roles(db) -> None:
    doctor = _user(db, "operador.norte")
    patient_account = _user(db, "paciente.demo")
    accountant = _user(db, "contable.eps")
    inactive = _user(db, "especialista.norte")
    inactive.is_active = False
    db.commit()

    async def scenario() -> int:
        conn = registry.subscribe(doctor.id, doctor.role.code, asyncio.get_running_loop())
        # Run the (sync) bus in a worker thread, as a route handler would.
        await asyncio.get_running_loop().run_in_executor(
            None,
            events.publish,
            events.DomainEvent(
                tipo="fhir_observacion",
                mensaje="resultado",
                recipients=(doctor.id, patient_account.id, accountant.id, inactive.id),
                paciente_id=None,
            ),
            db,
        )
        await asyncio.sleep(0.05)
        return conn.queue.qsize()

    assert _run(scenario()) == 1
    rows = list(db.scalars(select(Notification)))
    assert [r.user_id for r in rows] == [doctor.id]


def test_non_clinical_events_may_reach_the_accountant(db) -> None:
    accountant = _user(db, "contable.eps")
    events.publish(
        events.DomainEvent(tipo="factura", mensaje="m", recipients=(accountant.id,), clinical=False),
        db,
    )
    assert [r.user_id for r in db.scalars(select(Notification))] == [accountant.id]
