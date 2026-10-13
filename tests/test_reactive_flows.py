# ruff: noqa: F811
"""Epic 7 -- the R19 flows (each one a domain event and a reactor), the R18
counters, the R17 leftovers (type filter, locked tokens) and logging.

Recipients are checked from BOTH sides: the right people get the inbox row, and
nobody else does (the patient and the accountant never receive clinical events).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import mongomock
import pytest
from starlette.testclient import TestClient

from app import events
from app.api.deps import get_current_user
from app.billing import store, tariffs
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.logging import configure_logging
from app.core.security import create_access_token
from app.events.channel import parse_tipos, registry
from app.main import app
from app.models import Notification
from app.models.enums import AuditAction, RoleCode
from app.services import referrals as referrals_service
from app.services.auth import MAX_FAILED_ATTEMPTS
from tests.reactive_helpers import (
    PENDING,
    REFERRALS,
    DownCollection,
    actors,
    add_specialist,
    audit_rows,
    inbox,
    refer,
)
from tests.test_ai_reports import _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

FACTURAS = "/api/v1/facturas"
EVERYBODY = (
    "admin", "coordinador.eps", "contable.eps", "operador.norte", "operador.sur",
    "especialista.norte", "especialista.sur", "paciente.demo",
)


@pytest.fixture
def scene(db, client_as, ml):
    patient = _create_patient(db, client_as)
    sur = add_specialist(db, username="especialista.sur", org_code="IPS-SUR")
    return db, actors(client_as), patient, sur


def _tipos(db, username: str) -> list[str]:
    return [n.kind for n in inbox(db, username)]


def _remision_notes(db, username: str) -> list[Notification]:
    return [n for n in inbox(db, username) if n.kind.startswith("remision_")]


# ---------------------------------------------------------------------------
# Flow 1: referral created -> the specialist (and only the specialist)
# ---------------------------------------------------------------------------


def test_creating_a_referral_notifies_only_the_specialist(scene) -> None:
    db, client_as, patient, sur = scene
    response = refer(client_as("operador.norte"), patient["id"], sur, "Dolor toracico")
    assert response.status_code == 201, response.text
    referral_id = response.json()["id"]

    (note,) = inbox(db, "especialista.sur", "remision_creada")
    assert note.patient_id == uuid.UUID(patient["id"]) and not note.is_read
    assert note.data["remision_id"] == referral_id and note.data["estado"] == "pendiente"
    assert "Dolor toracico" in note.message and "Nueva remision" in note.message

    shown = client_as("especialista.sur").get("/api/v1/notifications?leida=false").json()
    created = [n for n in shown if n["tipo"] == "remision_creada"]
    assert len(created) == 1
    assert {"id", "tipo", "mensaje", "leida", "fecha", "paciente_id"} <= set(created[0])
    assert created[0]["paciente_id"] == patient["id"] and created[0]["leida"] is False

    # Nobody else: not the sender, not the patient, not the accountant.
    for username in EVERYBODY:
        if username != "especialista.sur":
            assert _remision_notes(db, username) == [], username
    assert inbox(db, "paciente.demo") == [] and inbox(db, "contable.eps") == []


def test_the_live_channel_carries_the_referral_to_the_specialist(scene) -> None:
    db, client_as, patient, sur = scene
    medico = _user(db, "operador.norte")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            f"/api/v1/events/ws?token={create_access_token(sur.id, sur.role.code.value)}"
        ) as ws:
            deadline = time.monotonic() + 3
            while registry.connection_count(sur.id) != 1:
                assert time.monotonic() < deadline, "the socket never subscribed"
                time.sleep(0.01)
            referrals_service.create(
                db, actor=medico, patient_id=uuid.UUID(patient["id"]),
                specialist_id=sur.id, motivo="Control",
            )
            first = ws.receive_json()
            second = ws.receive_json()
    registry.clear()
    assert first["tipo"] == "remision_creada" and first["paciente_id"] == patient["id"]
    assert first["mensaje"] and first["fecha"] and first["leida"] is False
    assert second["tipo"] == "pendientes_actualizados"
    assert second["datos"]["remisiones_recibidas"] == 1


# ---------------------------------------------------------------------------
# Flow 2: referral accepted -> the sender, and the invoice
# ---------------------------------------------------------------------------


def _accept(client_as, patient, sur) -> str:  # noqa: ANN001
    referral_id = refer(client_as("operador.norte"), patient["id"], sur).json()["id"]
    assert client_as("especialista.sur").post(f"{REFERRALS}/{referral_id}/accept").status_code == 200
    return referral_id


def test_accepting_notifies_the_sender_and_bills_the_referral(scene) -> None:
    db, client_as, patient, sur = scene
    referral_id = _accept(client_as, patient, sur)

    (note,) = inbox(db, "operador.norte", "remision_aceptada")
    assert note.patient_id == uuid.UUID(patient["id"])
    assert note.data["remision_id"] == referral_id and sur.full_name in note.message
    assert inbox(db, "especialista.sur", "remision_aceptada") == []

    # bill_referral ran: one invoice linked by remision_id, for the right patient.
    invoices = client_as("contable.eps").get(f"{FACTURAS}?servicio=remision").json()
    assert [i["remision_id"] for i in invoices] == [referral_id]
    assert invoices[0]["paciente_id"] == patient["id"]
    assert invoices[0]["valor"] == tariffs.REMISION and invoices[0]["origen"] == "automatica"
    assert invoices[0]["ips_id"] == str(sur.organization_id)  # the IPS that provides it
    # The accountant and the admin hear about the invoice, nobody clinical does.
    for username in ("contable.eps", "admin"):
        (invoice_note,) = inbox(db, username, "factura_creada")
        assert invoice_note.data["servicio"] == "remision"
    for username in ("operador.norte", "especialista.sur", "paciente.demo", "operador.sur"):
        assert inbox(db, username, "factura_creada") == [], username
    assert inbox(db, "paciente.demo") == []


def test_billing_the_referral_is_idempotent(scene) -> None:
    db, client_as, patient, sur = scene
    referral_id = _accept(client_as, patient, sur)

    replay = events.DomainEvent(
        tipo="remision_aceptada", mensaje="again", recipients=(),
        paciente_id=uuid.UUID(patient["id"]),
        datos={"remision_id": referral_id, "ips_id": str(sur.organization_id)},
    )
    events.publish(replay, db)
    events.publish(replay, db)
    assert store.get_repository().collection.count_documents({"servicio": "remision"}) == 1
    assert len(inbox(db, "contable.eps", "factura_creada")) == 1


def test_a_mongo_outage_never_fails_the_accept_and_reconciliation_recovers(scene) -> None:
    db, client_as, patient, sur = scene
    store.set_collection(DownCollection())
    referral_id = _accept(client_as, patient, sur)
    # The accept went through and the sender was told, with Mongo down.
    assert client_as("especialista.sur").get(f"{REFERRALS}/{referral_id}").json()["estado"] == "aceptada"
    assert len(inbox(db, "operador.norte", "remision_aceptada")) == 1

    store.set_collection(mongomock.MongoClient(tz_aware=True)["t"]["facturas"])
    body = client_as("contable.eps").post(f"{FACTURAS}/reconciliar").json()
    assert body["remision"] == {"revisados": 1, "creadas": 1, "ya_facturados": 0}
    invoices = client_as("contable.eps").get(f"{FACTURAS}?servicio=remision").json()
    assert [i["remision_id"] for i in invoices] == [referral_id]
    again = client_as("admin").post(f"{FACTURAS}/reconciliar").json()
    assert again["remision"]["ya_facturados"] == 1 and again["remision"]["creadas"] == 0


def test_rejecting_and_completing_notify_the_sender(scene) -> None:
    db, client_as, patient, sur = scene
    medico, specialist = client_as("operador.norte"), client_as("especialista.sur")
    rejected_id = refer(medico, patient["id"], sur).json()["id"]
    specialist.post(f"{REFERRALS}/{rejected_id}/reject", json={"motivo": "Fuera de mi area"})
    (rejected,) = inbox(db, "operador.norte", "remision_rechazada")
    assert "Fuera de mi area" in rejected.message and rejected.data["remision_id"] == rejected_id

    done_id = _accept(client_as, patient, sur)
    specialist.post(f"{REFERRALS}/{done_id}/report", json={"informe": "Todo bien"})
    (done,) = inbox(db, "operador.norte", "remision_completada")
    assert done.data["remision_id"] == done_id
    # A rejection is not billed; nobody but the sender hears about either.
    assert store.get_repository().collection.count_documents({"servicio": "remision"}) == 1
    for username in ("paciente.demo", "contable.eps", "operador.sur", "especialista.sur"):
        assert [n for n in _remision_notes(db, username) if n.kind != "remision_creada"] == []


# ---------------------------------------------------------------------------
# Flow 4: lockout -> every admin
# ---------------------------------------------------------------------------


def _login(client, username: str, password: str):  # noqa: ANN001
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_a_locked_account_alerts_the_admins_with_the_username(db, client_as) -> None:
    client = client_as("operador.norte")
    codes = [_login(client, "operador.sur", "mal").status_code for _ in range(MAX_FAILED_ATTEMPTS)]
    assert codes == [401, 401, 423]
    # The notification is there once the lock is committed, and says who.
    (note,) = inbox(db, "admin", "cuenta_bloqueada")
    assert "operador.sur" in note.message
    assert note.data["usuario"] == "operador.sur" and note.data["intentos"] == MAX_FAILED_ATTEMPTS
    assert note.patient_id is None
    shown = client_as("admin").get("/api/v1/notifications").json()
    assert [n["tipo"] for n in shown if n["tipo"] == "cuenta_bloqueada"] == ["cuenta_bloqueada"]
    # Not the locked user, not the other clinicians, not the patient, not the accountant.
    for username in EVERYBODY:
        if username != "admin":
            assert inbox(db, username, "cuenta_bloqueada") == [], username
    # The lock itself is audited (R02) and a fourth try, even with the right password, fails.
    assert [r.metadata_json["username"] for r in audit_rows(db, AuditAction.ACCOUNT_LOCKED)] == ["operador.sur"]
    assert _login(client, "operador.sur", "Demo2026!").status_code == 423


def test_one_lock_one_notification_even_if_attempts_continue(db, client_as) -> None:
    client = client_as("operador.norte")
    for _ in range(MAX_FAILED_ATTEMPTS + 3):
        _login(client, "operador.sur", "mal")
    assert len(inbox(db, "admin", "cuenta_bloqueada")) == 1


def test_an_unknown_username_leaks_nothing_and_alerts_nobody(db, client_as) -> None:
    client = client_as("operador.norte")
    known = [_login(client, "operador.sur", "mal").json() for _ in range(2)]
    unknown = [_login(client, "no.existe", "mal").json() for _ in range(5)]
    # An unknown user answers like a known one that is not yet locked.
    assert {r["detail"] for r in known} == {r["detail"] for r in unknown} == {"Credenciales invalidas"}
    assert inbox(db, "admin", "cuenta_bloqueada") == []


def test_every_admin_is_told(db, client_as) -> None:
    from app.core.security import hash_password
    from app.models import User
    from app.models.identity import Role
    from sqlalchemy import select

    role = db.scalar(select(Role).where(Role.code == RoleCode.ADMIN))
    db.add(
        User(
            username="admin.dos", full_name="Segundo Admin", email="a2@saludred.local",
            password_hash=hash_password("Demo2026!"), role_id=role.id,
        )
    )
    db.commit()
    client = client_as("operador.norte")
    for _ in range(MAX_FAILED_ATTEMPTS):
        _login(client, "operador.sur", "mal")
    assert len(inbox(db, "admin", "cuenta_bloqueada")) == 1
    assert len(inbox(db, "admin.dos", "cuenta_bloqueada")) == 1


# ---------------------------------------------------------------------------
# Billing reconciliation: ONE summary, not one notification per invoice
# ---------------------------------------------------------------------------


def test_reconciliation_sends_a_single_summary_per_recipient(scene) -> None:
    db, client_as, patient, _sur = scene
    medico = client_as("operador.norte")
    store.set_collection(DownCollection())
    for _ in range(3):
        report = _analyze(medico, patient["id"]).json()
        assert medico.post(f"/api/v1/ai-reports/{report['id']}/approve").status_code == 200
    store.set_collection(mongomock.MongoClient(tz_aware=True)["t3"]["facturas"])

    body = client_as("contable.eps").post(f"{FACTURAS}/reconciliar").json()
    created = body["total_creadas"]
    assert body["analisis_ia"]["creadas"] == 3 and created >= 3

    for username in ("contable.eps", "admin"):
        # No flood: the per-invoice announcement is NOT used here.
        assert inbox(db, username, "factura_creada") == [], username
        (summary,) = inbox(db, username, "facturas_conciliadas")
        assert summary.message == f"Conciliacion: se crearon {created} facturas"
        assert summary.data["creadas"] == created and summary.data["por_servicio"]["analisis_ia"] == 3
        assert summary.patient_id is None
    for username in ("operador.norte", "especialista.norte", "paciente.demo", "coordinador.eps"):
        assert inbox(db, username, "facturas_conciliadas") == [], username
    # A second pass creates nothing and says nothing.
    client_as("contable.eps").post(f"{FACTURAS}/reconciliar")
    assert len(inbox(db, "contable.eps", "facturas_conciliadas")) == 1


def test_a_single_approval_still_notifies_once_per_invoice(scene) -> None:
    db, client_as, patient, _sur = scene
    medico = client_as("operador.norte")
    report = _analyze(medico, patient["id"]).json()
    medico.post(f"/api/v1/ai-reports/{report['id']}/approve")
    assert len(inbox(db, "contable.eps", "factura_creada")) == 1
    assert inbox(db, "contable.eps", "facturas_conciliadas") == []


# ---------------------------------------------------------------------------
# R18: pending counters
# ---------------------------------------------------------------------------


def _pending(client_as, username: str) -> dict[str, int]:  # noqa: ANN001
    response = client_as(username).get(PENDING)
    assert response.status_code == 200, (username, response.text)
    return response.json()


@pytest.mark.parametrize("username", EVERYBODY)
def test_every_role_gets_the_three_counters(scene, username) -> None:
    _db, client_as, _patient, _sur = scene
    body = _pending(client_as, username)
    assert set(body) == {"reportes_por_revisar", "remisiones_recibidas", "alertas_activas"}
    assert all(isinstance(value, int) and value >= 0 for value in body.values())


def test_paciente_and_contable_have_nothing_pending_even_when_work_exists(scene) -> None:
    _db, client_as, patient, sur = scene
    _analyze(client_as("operador.norte"), patient["id"])
    refer(client_as("operador.norte"), patient["id"], sur)
    zero = {"reportes_por_revisar": 0, "remisiones_recibidas": 0, "alertas_activas": 0}
    assert _pending(client_as, "paciente.demo") == zero
    assert _pending(client_as, "contable.eps") == zero
    assert _pending(client_as, "admin")["reportes_por_revisar"] == 0
    assert _pending(client_as, "coordinador.eps")["remisiones_recibidas"] == 0


def test_counters_go_up_when_work_is_born_and_down_when_it_is_resolved(scene) -> None:
    db, client_as, patient, sur = scene
    medico, specialist = client_as("operador.norte"), client_as("especialista.sur")
    base_medico = _pending(client_as, "operador.norte")
    base_colleague = _pending(client_as, "especialista.norte")
    base_other_ips = _pending(client_as, "operador.sur")

    report = _analyze(medico, patient["id"]).json()  # critical with the fake model (0.91)
    after = _pending(client_as, "operador.norte")
    assert after["reportes_por_revisar"] == base_medico["reportes_por_revisar"] + 1
    assert after["alertas_activas"] == base_medico["alertas_activas"] + 1
    # A colleague of the same IPS sees the same work; another IPS does not.
    colleague = _pending(client_as, "especialista.norte")
    assert colleague["reportes_por_revisar"] == base_colleague["reportes_por_revisar"] + 1
    assert colleague["alertas_activas"] == base_colleague["alertas_activas"] + 1
    assert _pending(client_as, "operador.sur") == base_other_ips

    alert_id = medico.get("/api/v1/alerts?estado=activa").json()[0]["id"]
    assert medico.post(f"/api/v1/alerts/{alert_id}/acknowledge", json={"nota": "Visto"}).status_code == 200
    assert _pending(client_as, "operador.norte")["alertas_activas"] == base_medico["alertas_activas"]

    medico.post(f"/api/v1/ai-reports/{report['id']}/approve")
    assert _pending(client_as, "operador.norte")["reportes_por_revisar"] == base_medico["reportes_por_revisar"]

    # Referrals: the specialist's counter follows the pending referrals.
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 0
    first = refer(medico, patient["id"], sur).json()["id"]
    second = refer(medico, patient["id"], sur).json()["id"]
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 2
    specialist.post(f"{REFERRALS}/{first}/accept")
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 1
    specialist.post(f"{REFERRALS}/{second}/reject", json={"motivo": "no"})
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 0

    # A rejected report leaves the counter too.
    other = _analyze(medico, patient["id"]).json()
    assert _pending(client_as, "operador.norte")["reportes_por_revisar"] == base_medico["reportes_por_revisar"] + 1
    medico.post(f"/api/v1/ai-reports/{other['id']}/reject", json={"motivo": "Datos incompletos"})
    assert _pending(client_as, "operador.norte")["reportes_por_revisar"] == base_medico["reportes_por_revisar"]


def test_the_referral_grant_also_moves_the_specialists_report_counter(scene) -> None:
    db, client_as, patient, sur = scene
    medico = client_as("operador.norte")
    assert _pending(client_as, "especialista.sur")["reportes_por_revisar"] == 0
    _analyze(medico, patient["id"])
    # Not in the specialist's scope yet: no referral, other IPS.
    assert _pending(client_as, "especialista.sur")["reportes_por_revisar"] == 0
    refer(medico, patient["id"], sur)
    assert _pending(client_as, "especialista.sur")["reportes_por_revisar"] == 1


def test_a_deleted_patient_does_not_count(scene) -> None:
    _db, client_as, patient, sur = scene
    medico = client_as("operador.norte")
    base = _pending(client_as, "operador.norte")
    _analyze(medico, patient["id"])
    refer(medico, patient["id"], sur)
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 1
    assert medico.delete(f"/api/v1/patients/{patient['id']}").status_code == 204
    assert _pending(client_as, "operador.norte") == base
    assert _pending(client_as, "especialista.sur")["remisiones_recibidas"] == 0


def test_pendientes_actualizados_is_live_only(scene) -> None:
    """It reaches the open sockets but never fills the inbox."""

    db, client_as, patient, sur = scene
    refer(client_as("operador.norte"), patient["id"], sur)
    client_as("especialista.sur").post(
        f"{REFERRALS}/{refer(client_as('operador.norte'), patient['id'], sur).json()['id']}/accept"
    )
    for username in EVERYBODY:
        assert "pendientes_actualizados" not in _tipos(db, username), username


# ---------------------------------------------------------------------------
# R17: ?tipos= filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None), ("", None), (" , ", None),
        ("a", frozenset({"a"})), ("a, b ,,c", frozenset({"a", "b", "c"})),
    ],
)
def test_parse_tipos(raw, expected) -> None:
    assert parse_tipos(raw) == expected


def test_the_registry_delivers_only_the_wanted_types() -> None:
    async def scenario() -> list[str]:
        loop = asyncio.get_running_loop()
        user = uuid.uuid4()
        conn = registry.subscribe(user, RoleCode.SPECIALIST, loop, frozenset({"alerta_critica"}))
        everything = registry.subscribe(user, RoleCode.SPECIALIST, loop)
        try:
            for tipo in ("remision_creada", "alerta_critica", "pendientes_actualizados"):
                registry.push(user, {"id": tipo, "tipo": tipo})
            await asyncio.sleep(0.02)
            assert everything.queue.qsize() == 3
            return [conn.queue.get_nowait()["tipo"] for _ in range(conn.queue.qsize())]
        finally:
            registry.unsubscribe(conn)
            registry.unsubscribe(everything)

    assert asyncio.run(scenario()) == ["alerta_critica"]


def test_the_ws_filters_by_type(scene) -> None:
    db, client_as, patient, sur = scene
    medico = _user(db, "operador.norte")
    app.dependency_overrides[get_db] = lambda: db
    token = create_access_token(sur.id, sur.role.code.value)
    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            f"/api/v1/events/ws?token={token}&tipos=remision_creada"
        ) as ws:
            deadline = time.monotonic() + 3
            while registry.connection_count(sur.id) != 1:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            referrals_service.create(
                db, actor=medico, patient_id=uuid.UUID(patient["id"]),
                specialist_id=sur.id, motivo="Control",
            )
            # Two events fire (the referral and the counters); only the wanted type arrives.
            events.publish(
                events.DomainEvent(tipo="otra_cosa", mensaje="x", recipients=(sur.id,)), db
            )
            referrals_service.create(
                db, actor=medico, patient_id=uuid.UUID(patient["id"]),
                specialist_id=sur.id, motivo="Otro control",
            )
            first, second = ws.receive_json(), ws.receive_json()
    registry.clear()
    assert first["tipo"] == second["tipo"] == "remision_creada"
    assert first["datos"]["remision_id"] != second["datos"]["remision_id"]


def test_the_sse_generator_filters_by_type() -> None:
    from app.api.routes.events import StreamIdentity, event_stream

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> str:
        identity = StreamIdentity(uuid.uuid4(), RoleCode.SPECIALIST)
        gen = event_stream(_Request(), identity, heartbeat_seconds=0.05, tipos=frozenset({"keep"}))
        await gen.__anext__()  # retry/connected
        registry.push(identity.user_id, {"id": "1", "tipo": "drop", "mensaje": "no"})
        registry.push(identity.user_id, {"id": "2", "tipo": "keep", "mensaje": "si"})
        frame = await gen.__anext__()
        while frame.startswith(":"):
            frame = await gen.__anext__()
        await gen.aclose()
        return frame

    frame = asyncio.run(scenario())
    assert json.loads(frame.split("data: ", 1)[1])["tipo"] == "keep"


# ---------------------------------------------------------------------------
# R17: a token issued before the lock stops working
# ---------------------------------------------------------------------------


def test_a_locked_or_disabled_account_is_rejected_on_every_request(db) -> None:
    user = _user(db, "operador.norte")
    token = create_access_token(user.id, user.role.code.value)
    headers = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        paths = ("/api/v1/auth/me", "/api/v1/notifications", PENDING, "/api/v1/alerts")
        assert all(client.get(p, headers=headers).status_code == 200 for p in paths)

        user.locked_at = datetime.now(timezone.utc)
        db.commit()
        for path in paths:
            response = client.get(path, headers=headers)
            assert response.status_code == 401, path
            assert response.json()["detail"] == "La cuenta esta bloqueada"
            assert response.headers["www-authenticate"] == "Bearer"
        assert client.post(REFERRALS, json={}, headers=headers).status_code == 401

        user.locked_at = None
        db.commit()
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

        user.is_active = False
        db.commit()
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_the_admin_unlock_restores_access_for_the_old_token(db, client_as) -> None:
    user = _user(db, "operador.sur")
    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role.code.value)}"}
    client = client_as("operador.norte")
    for _ in range(MAX_FAILED_ATTEMPTS):
        _login(client, "operador.sur", "mal")

    # Real authentication from here on: only the database is swapped.
    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get("/api/v1/auth/me", headers=headers).status_code == 401
    unlocked = client_as("admin").post(f"/api/v1/admin/users/{user.id}/unlock", json={})
    assert unlocked.status_code == 200, unlocked.text
    app.dependency_overrides.pop(get_current_user, None)
    assert TestClient(app).get("/api/v1/auth/me", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Logging and settings
# ---------------------------------------------------------------------------


def test_app_info_logs_reach_the_container_logs(capsys) -> None:
    root, app_logger = logging.getLogger(), logging.getLogger("app")
    saved_root, saved_app = root.handlers[:], app_logger.handlers[:]
    uvicorn_before = logging.getLogger("uvicorn.access").level
    root.handlers, app_logger.handlers = [], []  # what uvicorn leaves: no handlers
    try:
        configure_logging("INFO")
        configure_logging("INFO")  # idempotent
        assert len(app_logger.handlers) == 1
        logging.getLogger("app.billing.store").info("Billing ready: indexes ensured")
        logging.getLogger("app.billing.store").debug("hidden")
        err = capsys.readouterr().err
        assert "Billing ready: indexes ensured" in err and "hidden" not in err
        assert logging.getLogger("uvicorn.access").level == uvicorn_before  # untouched
    finally:
        root.handlers, app_logger.handlers = saved_root, saved_app
        configure_logging("INFO")


def test_a_wrong_log_level_falls_back_to_info() -> None:
    configure_logging("no-such-level")
    assert logging.getLogger("app").level == logging.INFO
    configure_logging("INFO")


def test_the_app_logger_is_at_info_after_import() -> None:
    assert logging.getLogger("app.billing.store").isEnabledFor(logging.INFO)


def test_the_reactive_settings_defaults() -> None:
    fields = Settings.model_fields
    assert fields["alert_escalation_minutes"].default == 5
    assert fields["alert_escalation_enabled"].default is True
    assert fields["alert_monitor_interval_seconds"].default == 30
    assert fields["bed_cleaning_alert_minutes"].default == 60
    assert fields["log_level"].default == "INFO"
    # The test suite turns the timer off (conftest); production leaves it on.
    assert get_settings().alert_escalation_enabled is False
