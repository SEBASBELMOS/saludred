# ruff: noqa: F811
"""Epic 6 -- billing in MongoDB (R09, R13, R19 flow 3).

MongoDB is replaced by ``mongomock`` (see ``tests/conftest.py``); no test needs a
real Mongo. PostgreSQL is SQLite in memory, as everywhere else in the suite.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from pymongo.errors import ServerSelectionTimeoutError
from sqlalchemy import select

from app import events
from app.billing import service as billing_service
from app.billing import store, tariffs
from app.core.security import hash_password
from app.models import AuditLog, BedAssignment, Location, Notification, Organization, Patient, User
from app.models.enums import (
    AuditAction,
    BedAssignmentStatus,
    OrganizationType,
    RoleCode,
)
from app.models.identity import Role
from tests.test_ai_reports import CANARY, _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

FACTURAS = "/api/v1/facturas"


class DownCollection:
    """A Mongo that does not answer: every operation times out."""

    def __getattr__(self, name: str):  # noqa: ANN204
        def fail(*_args: object, **_kwargs: object) -> None:
            raise ServerSelectionTimeoutError("test: mongo down")

        return fail


def _approve(client_as, patient_id: str) -> str:
    """The clinician analyzes and approves; returns the report id."""

    clinician = client_as("operador.norte")
    report = _analyze(clinician, patient_id)
    assert report.status_code == 201, report.text
    approved = clinician.post(f"/api/v1/ai-reports/{report.json()['id']}/approve")
    assert approved.status_code == 200, approved.text
    return report.json()["id"]


def _notifications(db, username: str, tipo: str = "factura_creada") -> list[Notification]:
    return list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == _user(db, username).id, Notification.kind == tipo
            )
        )
    )


@pytest.fixture
def scene(db, client_as, ml):
    patient = _create_patient(db, client_as)
    return db, client_as, patient


def _manual(client, patient_id: str, **overrides):
    body = {
        "paciente_id": patient_id,
        "servicio": "consulta",
        "descripcion": "Consulta de control",
        **overrides,
    }
    return client.post(FACTURAS, json=body)


def _other_eps_accountant(db) -> User:
    """A contable of a second EPS: same role, different organization."""

    eps = Organization(code="EPS-OTRA", name="Otra EPS", organization_type=OrganizationType.EPS)
    db.add(eps)
    db.flush()
    role = db.scalar(select(Role).where(Role.code == RoleCode.ACCOUNTANT))
    user = User(
        username="contable.otra", full_name="Contable Otra EPS", email="otra@saludred.local",
        password_hash=hash_password("Demo2026!"), role_id=role.id, organization_id=eps.id,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def other_accountant(db, client_as) -> str:
    _other_eps_accountant(db)
    return "contable.otra"


# ---------------------------------------------------------------------------
# R09 -- roles and manual invoices
# ---------------------------------------------------------------------------


def test_accountant_creates_and_lists_invoices(scene) -> None:
    db, client_as, patient = scene
    contable = client_as("contable.eps")

    created = _manual(contable, patient["id"], valor_unitario=90_000, cantidad=2)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["servicio"] == "consulta" and body["origen"] == "manual"
    assert body["valor"] == 180_000 and body["estado_pago"] == "pendiente"
    assert body["paciente_id"] == patient["id"]
    assert body["creado_por"] == str(_user(db, "contable.eps").id)
    assert [h["estado"] for h in body["historial"]] == ["pendiente"]

    listing = contable.get(FACTURAS)
    assert listing.status_code == 200
    assert [i["id"] for i in listing.json()] == [body["id"]]
    assert set(listing.json()[0]) >= {"id", "paciente_id", "servicio", "valor", "fecha", "estado_pago"}
    assert contable.get(f"{FACTURAS}/{body['id']}").json()["id"] == body["id"]
    # The admin operates billing too, across every EPS.
    assert client_as("admin").get(FACTURAS).json()[0]["id"] == body["id"]


def test_manual_invoice_rules(scene) -> None:
    _db, client_as, patient = scene
    contable = client_as("contable.eps")
    assert _manual(contable, patient["id"]).json()["valor_unitario"] == tariffs.CONSULTA
    # A medicamento must state its price; system services cannot be billed by hand.
    assert _manual(contable, patient["id"], servicio="medicamento").status_code == 422
    assert _manual(contable, patient["id"], servicio="medicamento", valor_unitario=12_000).status_code == 201
    for servicio in ("analisis_ia", "dia_cama", "remision"):
        assert _manual(contable, patient["id"], servicio=servicio).status_code == 422
    assert _manual(contable, str(uuid.uuid4())).status_code == 404
    assert _manual(contable, patient["id"], valor_unitario=-5).status_code == 422
    assert _manual(contable, patient["id"], ips_id=str(uuid.uuid4())).status_code == 422


@pytest.mark.parametrize(
    "username", ["operador.norte", "especialista.norte", "paciente.demo", "coordinador.eps"]
)
def test_other_roles_get_403_everywhere(scene, username) -> None:
    _db, client_as, patient = scene
    created = _manual(client_as("contable.eps"), patient["id"]).json()["id"]
    client = client_as(username)
    fid = f"{FACTURAS}/{created}"

    assert client.get(FACTURAS).status_code == 403
    assert client.get(fid).status_code == 403
    assert client.get(f"{FACTURAS}/resumen").status_code == 403
    assert client.get(f"{fid}/recibo.pdf").status_code == 403
    assert client.post(FACTURAS, json={"paciente_id": patient["id"], "servicio": "consulta",
                                       "descripcion": "Consulta"}).status_code == 403
    assert client.post(f"{fid}/pagar").status_code == 403
    assert client.post(f"{fid}/anular", json={"motivo": "error"}).status_code == 403
    assert client.post(f"{FACTURAS}/reconciliar").status_code == 403


def test_an_accountant_of_another_eps_sees_nothing(scene, other_accountant) -> None:
    _db, client_as, patient = scene
    created = _manual(client_as("contable.eps"), patient["id"]).json()["id"]
    other = client_as(other_accountant)

    assert other.get(FACTURAS).json() == []
    assert other.get(f"{FACTURAS}/{created}").status_code == 404
    assert other.get(f"{FACTURAS}/{created}/recibo.pdf").status_code == 404
    assert other.post(f"{FACTURAS}/{created}/pagar").status_code == 404
    assert other.post(f"{FACTURAS}/{created}/anular", json={"motivo": "intento"}).status_code == 404
    assert _manual(other, patient["id"]).status_code == 404  # patient of another EPS
    assert other.get(f"{FACTURAS}/resumen").json()["total_facturado"] == {"cantidad": 0, "valor": 0}
    # Still there for its own EPS.
    assert client_as("contable.eps").get(f"{FACTURAS}/{created}").json()["estado_pago"] == "pendiente"
    assert client_as("contable.eps").get(f"{FACTURAS}/not-an-object-id").status_code == 404


def test_list_filters_and_order(scene) -> None:
    _db, client_as, patient = scene
    contable = client_as("contable.eps")
    first = _manual(contable, patient["id"], descripcion="primera").json()
    second = _manual(contable, patient["id"], servicio="medicamento", valor_unitario=5_000).json()
    contable.post(f"{FACTURAS}/{first['id']}/pagar")

    ids = [i["id"] for i in contable.get(FACTURAS).json()]
    assert ids == [second["id"], first["id"]]  # newest first
    assert [i["id"] for i in contable.get(f"{FACTURAS}?estado_pago=pagada").json()] == [first["id"]]
    assert [i["id"] for i in contable.get(f"{FACTURAS}?servicio=medicamento").json()] == [second["id"]]
    assert len(contable.get(f"{FACTURAS}?paciente_id={patient['id']}").json()) == 2
    assert contable.get(f"{FACTURAS}?paciente_id={uuid.uuid4()}").json() == []
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert len(contable.get(f"{FACTURAS}?desde={today}&hasta={today}").json()) == 2
    assert contable.get(f"{FACTURAS}?hasta={yesterday}").json() == []
    assert contable.get(f"{FACTURAS}?desde=2999-01-01").json() == []
    assert contable.get(f"{FACTURAS}?estado_pago=nada").status_code == 422


def test_summary_by_state_and_service(scene) -> None:
    _db, client_as, patient = scene
    contable = client_as("contable.eps")
    a = _manual(contable, patient["id"], valor_unitario=100_000).json()
    b = _manual(contable, patient["id"], servicio="medicamento", valor_unitario=30_000).json()
    c = _manual(contable, patient["id"], valor_unitario=50_000).json()
    contable.post(f"{FACTURAS}/{a['id']}/pagar")
    contable.post(f"{FACTURAS}/{c['id']}/anular", json={"motivo": "duplicada"})

    summary = contable.get(f"{FACTURAS}/resumen").json()
    assert summary["por_estado"]["pagada"] == {"cantidad": 1, "valor": 100_000}
    assert summary["por_estado"]["pendiente"] == {"cantidad": 1, "valor": 30_000}
    assert summary["por_estado"]["anulada"] == {"cantidad": 1, "valor": 50_000}
    # Voided invoices are not owed: they stay out of the per-service and the grand total.
    assert summary["por_servicio"]["consulta"] == {"cantidad": 1, "valor": 100_000}
    assert summary["por_servicio"]["medicamento"] == {"cantidad": 1, "valor": 30_000}
    assert summary["por_servicio"]["dia_cama"] == {"cantidad": 0, "valor": 0}
    assert summary["total_facturado"] == {"cantidad": 2, "valor": 130_000}
    assert b["id"]


# ---------------------------------------------------------------------------
# State machine and history
# ---------------------------------------------------------------------------


def test_state_machine_and_history(scene) -> None:
    db, client_as, patient = scene
    contable = client_as("contable.eps")

    paid = _manual(contable, patient["id"]).json()["id"]
    response = contable.post(f"{FACTURAS}/{paid}/pagar", json={"nota": "Transferencia 123"})
    assert response.status_code == 200 and response.json()["estado_pago"] == "pagada"
    # pagada is terminal: neither pay again nor void.
    assert contable.post(f"{FACTURAS}/{paid}/pagar").status_code == 409
    assert contable.post(f"{FACTURAS}/{paid}/anular", json={"motivo": "reversa"}).status_code == 409

    voided = _manual(contable, patient["id"]).json()["id"]
    assert contable.post(f"{FACTURAS}/{voided}/anular", json={}).status_code == 422  # motivo required
    assert contable.post(f"{FACTURAS}/{voided}/anular", json={"motivo": "  "}).status_code == 422
    response = contable.post(f"{FACTURAS}/{voided}/anular", json={"motivo": "Paciente equivocado"})
    assert response.status_code == 200 and response.json()["estado_pago"] == "anulada"
    # anulada is terminal as well.
    assert contable.post(f"{FACTURAS}/{voided}/pagar").status_code == 409
    assert contable.post(f"{FACTURAS}/{voided}/anular", json={"motivo": "otra vez"}).status_code == 409

    history = contable.get(f"{FACTURAS}/{paid}").json()["historial"]
    assert [(h["estado"], h["nota"]) for h in history] == [
        ("pendiente", "Factura creada"), ("pagada", "Transferencia 123"),
    ]
    assert history[1]["usuario"] == str(_user(db, "contable.eps").id)
    voided_history = contable.get(f"{FACTURAS}/{voided}").json()["historial"]
    assert [h["estado"] for h in voided_history] == ["pendiente", "anulada"]
    assert voided_history[1]["nota"] == "Paciente equivocado"
    # Nothing is hard-deleted: both are still listed.
    assert {i["id"] for i in contable.get(FACTURAS).json()} == {paid, voided}


def test_payment_and_voiding_are_audited(scene) -> None:
    db, client_as, patient = scene
    contable = client_as("contable.eps")
    first = _manual(contable, patient["id"]).json()["id"]
    second = _manual(contable, patient["id"]).json()["id"]
    contable.post(f"{FACTURAS}/{first}/pagar")
    contable.post(f"{FACTURAS}/{second}/anular", json={"motivo": "error de digitacion"})

    def entries(action: AuditAction) -> list[AuditLog]:
        return list(db.scalars(select(AuditLog).where(AuditLog.action == action)))

    assert {e.metadata_json["factura_id"] for e in entries(AuditAction.INVOICE_CREATED)} == {first, second}
    (paid,) = entries(AuditAction.INVOICE_PAID)
    assert paid.metadata_json["factura_id"] == first
    assert paid.metadata_json["paciente_id"] == patient["id"]
    assert paid.metadata_json["desde"] == "pendiente"
    (voided,) = entries(AuditAction.INVOICE_VOIDED)
    assert voided.metadata_json["factura_id"] == second and voided.user_id == _user(db, "contable.eps").id


def test_concurrent_transition_loses_cleanly(scene) -> None:
    """The state check is part of the update filter: a stale read cannot win."""

    db, client_as, patient = scene
    contable = client_as("contable.eps")
    fid = _manual(contable, patient["id"]).json()["id"]
    repo = store.get_repository()
    from app.billing.documents import EstadoPago

    won = repo.transition(
        store.parse_id(fid), to=EstadoPago.PAGADA, allowed_from=(EstadoPago.PENDIENTE,),
        usuario="a", nota=None,
    )
    lost = repo.transition(
        store.parse_id(fid), to=EstadoPago.ANULADA, allowed_from=(EstadoPago.PENDIENTE,),
        usuario="b", nota=None,
    )
    assert won is not None and lost is None
    assert contable.get(f"{FACTURAS}/{fid}").json()["estado_pago"] == "pagada"


# ---------------------------------------------------------------------------
# R19 flow 3 -- approving a report bills it
# ---------------------------------------------------------------------------


def test_approval_creates_exactly_one_invoice_with_reporte_id(scene) -> None:
    db, client_as, patient = scene
    report_id = _approve(client_as, patient["id"])

    listing = client_as("contable.eps").get(FACTURAS).json()
    assert len(listing) == 1
    invoice = listing[0]
    assert invoice["reporte_id"] == report_id and invoice["paciente_id"] == patient["id"]
    assert invoice["servicio"] == "analisis_ia" and invoice["valor"] == tariffs.ANALISIS_IA
    assert invoice["origen"] == "automatica" and invoice["creado_por"] == "sistema"
    assert invoice["estado_pago"] == "pendiente"
    assert invoice["eps_id"] == str(db.get(Patient, uuid.UUID(patient["id"])).eps_organization_id)
    assert invoice["ips_id"] == str(_user(db, "operador.norte").organization_id)

    audit = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.INVOICE_CREATED))
    assert audit.metadata_json["paciente_id"] == patient["id"]
    assert audit.metadata_json["reporte_id"] == report_id
    assert audit.metadata_json["factura_id"] == invoice["id"]
    assert audit.user_id is None  # created by the system, not by a person


def test_a_rejected_report_is_not_billed(scene) -> None:
    _db, client_as, patient = scene
    clinician = client_as("operador.norte")
    report = _analyze(clinician, patient["id"]).json()
    clinician.post(f"/api/v1/ai-reports/{report['id']}/reject", json={"motivo": "datos incompletos"})
    assert client_as("contable.eps").get(FACTURAS).json() == []


def test_replaying_the_event_does_not_duplicate_or_renotify(scene) -> None:
    db, client_as, patient = scene
    report_id = _approve(client_as, patient["id"])
    before = len(_notifications(db, "contable.eps"))
    assert before == 1

    for _ in range(3):
        events.publish(
            events.DomainEvent(
                tipo="reporte_aprobado", mensaje="x", recipients=(),
                datos={"reporte_id": report_id},
            ),
            db,
        )
    assert len(client_as("contable.eps").get(FACTURAS).json()) == 1
    assert len(_notifications(db, "contable.eps")) == before
    assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.INVOICE_CREATED)))) == 1


def test_who_is_told_about_a_new_invoice(scene, other_accountant) -> None:
    db, client_as, patient = scene
    report_id = _approve(client_as, patient["id"])
    invoice_id = client_as("contable.eps").get(FACTURAS).json()[0]["id"]

    for username in ("contable.eps", "admin"):
        (note,) = _notifications(db, username)
        assert note.patient_id == uuid.UUID(patient["id"])
        assert note.data["factura_id"] == invoice_id and note.data["reporte_id"] == report_id
        shown = client_as(username).get("/api/v1/notifications?leida=false").json()
        assert [n["tipo"] for n in shown if n["tipo"] == "factura_creada"] == ["factura_creada"]
    # Not the medico, not the especialista, not the patient, not the coordinator,
    # and not the accountant of another EPS.
    for username in ("operador.norte", "operador.sur", "especialista.norte", "paciente.demo",
                     "coordinador.eps", other_accountant):
        assert _notifications(db, username) == [], username
        assert all(n["tipo"] != "factura_creada" for n in client_as(username).get("/api/v1/notifications").json())


def test_billing_event_reaches_the_live_channel(scene) -> None:
    import asyncio

    from app.events.channel import registry

    db, client_as, patient = scene
    accountant = _user(db, "contable.eps")
    medico = _user(db, "operador.norte")

    async def run() -> tuple[list[str], list[str]]:
        loop = asyncio.get_running_loop()
        mine = registry.subscribe(accountant.id, accountant.role.code, loop)
        theirs = registry.subscribe(medico.id, medico.role.code, loop)
        try:
            await loop.run_in_executor(None, lambda: _approve(client_as, patient["id"]))
            await asyncio.sleep(0.05)
            return (
                [mine.queue.get_nowait()["tipo"] for _ in range(mine.queue.qsize())],
                [theirs.queue.get_nowait()["tipo"] for _ in range(theirs.queue.qsize())],
            )
        finally:
            registry.unsubscribe(mine)
            registry.unsubscribe(theirs)

    got_accountant, got_medico = asyncio.run(run())
    assert got_accountant == ["factura_creada"]
    # The medico hears about the clinical flow (alert, counters), never the invoice.
    assert "factura_creada" not in got_medico


# ---------------------------------------------------------------------------
# Bed-days
# ---------------------------------------------------------------------------


def _active_assignment(db, service_contains: str | None = None) -> BedAssignment:
    stmt = (
        select(BedAssignment)
        .join(Location, Location.id == BedAssignment.location_id)
        .where(BedAssignment.status == BedAssignmentStatus.ACTIVE)
    )
    if service_contains:
        stmt = stmt.where(Location.service.contains(service_contains))
    assignment = db.scalars(stmt).first()
    assert assignment is not None, service_contains
    return assignment


@pytest.mark.parametrize(
    ("service", "hours", "days", "category"),
    [("Hospitalizacion", 30, 2, "hospitalizacion"), ("UCI", 1, 1, "uci"), ("UCI", 72.5, 4, "uci")],
)
def test_releasing_a_bed_bills_the_stay(db, client_as, service, hours, days, category) -> None:
    assignment = _active_assignment(db, service)
    bed = db.get(Location, assignment.location_id)
    assignment.assigned_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db.commit()

    response = client_as("admin").post(f"/api/v1/beds/{bed.id}/release")
    assert response.status_code == 200, response.text

    (invoice,) = [i for i in client_as("admin").get(f"{FACTURAS}?servicio=dia_cama").json()]
    assert invoice["cantidad"] == days
    assert invoice["valor_unitario"] == tariffs.BED_DAY[category]
    assert invoice["valor"] == days * tariffs.BED_DAY[category]
    assert invoice["solicitud_cama_id"] == str(assignment.bed_request_id)
    assert invoice["cama_id"] == str(bed.id)
    assert invoice["ips_id"] == str(bed.organization_id) and invoice["origen"] == "automatica"
    assert invoice["reporte_id"] is None
    assert len(_notifications(db, "contable.eps")) == 1

    # Releasing again is a 409 and never bills twice.
    assert client_as("admin").post(f"/api/v1/beds/{bed.id}/release").status_code == 409
    assert len(client_as("admin").get(f"{FACTURAS}?servicio=dia_cama").json()) == 1


def test_stay_days_rounds_up_with_a_floor_of_one() -> None:
    start = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    assert billing_service.stay_days(start, start) == 1
    assert billing_service.stay_days(start, start + timedelta(hours=24)) == 1
    assert billing_service.stay_days(start, start + timedelta(hours=24, minutes=1)) == 2
    # SQLite hands back naive datetimes: treated as UTC.
    assert billing_service.stay_days(start.replace(tzinfo=None), start + timedelta(days=3)) == 3


def test_bed_day_tariff_by_service() -> None:
    assert tariffs.bed_day_tariff("UCI Adultos") == ("uci", 1_850_000)
    assert tariffs.bed_day_tariff("Cuidados intensivos") == ("uci", 1_850_000)
    assert tariffs.bed_day_tariff("Urgencias") == ("urgencias", 260_000)
    assert tariffs.bed_day_tariff("Hospitalización") == ("hospitalizacion", 380_000)
    assert tariffs.bed_day_tariff(None) == ("hospitalizacion", 380_000)


# ---------------------------------------------------------------------------
# Mongo down, reconciliation
# ---------------------------------------------------------------------------


def test_mongo_down_never_breaks_approval_or_release(scene) -> None:
    db, client_as, patient = scene
    store.set_collection(DownCollection())

    report = _analyze(client_as("operador.norte"), patient["id"]).json()
    approved = client_as("operador.norte").post(f"/api/v1/ai-reports/{report['id']}/approve")
    assert approved.status_code == 200 and approved.json()["estado"] == "aprobado"

    assignment = _active_assignment(db)
    released = client_as("admin").post(f"/api/v1/beds/{assignment.location_id}/release")
    assert released.status_code == 200

    for call in (
        lambda c: c.get(FACTURAS),
        lambda c: c.get(f"{FACTURAS}/resumen"),
        lambda c: c.get(f"{FACTURAS}/{'a' * 24}"),
        lambda c: c.post(f"{FACTURAS}/reconciliar"),
        lambda c: _manual(c, patient["id"]),
    ):
        for username in ("contable.eps", "admin"):
            response = call(client_as(username))
            assert response.status_code == 503, response.text
            assert "contabilidad" in response.json()["detail"].lower()
    # Authorization still comes first: the medico gets 403, not 503.
    assert client_as("operador.norte").get(FACTURAS).status_code == 403
    assert _notifications(db, "contable.eps") == []


def test_unconfigured_mongo_answers_503(scene, monkeypatch) -> None:
    _db, client_as, _patient = scene
    store.set_collection(None)
    monkeypatch.setattr(store, "_real", None)
    monkeypatch.setattr(store.get_settings(), "mongo_url", None)
    assert client_as("contable.eps").get(FACTURAS).status_code == 503
    assert store.init_billing() is False  # never raises


def test_a_malformed_mongo_url_does_not_crash_startup(monkeypatch) -> None:
    store.set_collection(None)
    monkeypatch.setattr(store, "_real", None)
    monkeypatch.setattr(store.get_settings(), "mongo_url", "http://not-a-mongo-url")
    assert store.init_billing() is False


def test_reconciliation_creates_the_missing_invoices_and_is_idempotent(scene) -> None:
    db, client_as, patient = scene
    store.set_collection(DownCollection())
    report = _analyze(client_as("operador.norte"), patient["id"]).json()
    client_as("operador.norte").post(f"/api/v1/ai-reports/{report['id']}/approve")
    assignment = _active_assignment(db)
    client_as("admin").post(f"/api/v1/beds/{assignment.location_id}/release")

    # Mongo comes back (empty): nothing was billed while it was down.
    store.set_collection(mongomock.MongoClient(tz_aware=True)["t"]["facturas"])
    assert client_as("admin").get(FACTURAS).json() == []

    # The contable of the patient's EPS reconciles; its scope is its EPS.
    first = client_as("contable.eps").post(f"{FACTURAS}/reconciliar")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["analisis_ia"]["creadas"] == 1
    assert body["dia_cama"]["creadas"] >= 1
    assert body["total_creadas"] == body["analisis_ia"]["creadas"] + body["dia_cama"]["creadas"]
    by_service = {i["servicio"] for i in client_as("admin").get(FACTURAS).json()}
    assert by_service == {"analisis_ia", "dia_cama"}
    ia = client_as("admin").get(f"{FACTURAS}?servicio=analisis_ia").json()
    assert [i["reporte_id"] for i in ia] == [report["id"]]
    # ONE summary per recipient, never one notification per invoice.
    assert _notifications(db, "contable.eps") == []
    summary = _notifications(db, "contable.eps", "facturas_conciliadas")
    assert [n.message for n in summary] == [
        f"Conciliacion: se crearon {body['total_creadas']} facturas"
    ]

    second = client_as("admin").post(f"{FACTURAS}/reconciliar").json()
    assert second["total_creadas"] == 0
    assert second["analisis_ia"]["ya_facturados"] == 1
    assert len(client_as("admin").get(FACTURAS).json()) == body["total_creadas"]


def test_reconciliation_respects_the_eps_scope(scene, other_accountant) -> None:
    _db, client_as, patient = scene
    store.set_collection(DownCollection())
    _approve_while_down = _analyze(client_as("operador.norte"), patient["id"]).json()
    client_as("operador.norte").post(f"/api/v1/ai-reports/{_approve_while_down['id']}/approve")
    store.set_collection(mongomock.MongoClient(tz_aware=True)["t2"]["facturas"])

    other = client_as(other_accountant).post(f"{FACTURAS}/reconciliar").json()
    assert other["total_creadas"] == 0 and other["analisis_ia"]["revisados"] == 0
    assert client_as("contable.eps").post(f"{FACTURAS}/reconciliar").json()["analisis_ia"]["creadas"] == 1


# ---------------------------------------------------------------------------
# Idempotency in the database, indexes
# ---------------------------------------------------------------------------


def test_duplicate_automatic_invoice_is_already_billed_not_an_error(scene) -> None:
    db, client_as, patient = scene
    report_id = _approve(client_as, patient["id"])
    again = billing_service.bill_report(db, uuid.UUID(report_id))
    assert again is not None and again.created is False
    assert again.factura["reporte_id"] == report_id
    assert store.get_repository().collection.count_documents({}) == 1


def test_indexes_are_idempotent_unique_and_partial(mongo_facturas) -> None:
    repo = store.get_repository()
    repo.ensure_indexes()
    repo.ensure_indexes()
    info = mongo_facturas.index_information()
    assert {"uq_servicio_reporte", "uq_servicio_solicitud_cama", "uq_servicio_remision"} <= set(info)
    assert info["uq_servicio_reporte"]["unique"] is True
    assert info["uq_servicio_reporte"]["partialFilterExpression"] == {"reporte_id": {"$exists": True}}
    # Manual invoices carry no link, so the partial indexes never constrain them.
    from app.billing.documents import MANUAL_SERVICES, Origen, new_document

    for _ in range(2):
        doc = new_document(
            paciente_id=uuid.uuid4(), eps_id=None, ips_id=None, servicio=MANUAL_SERVICES[0],
            descripcion="x", cantidad=1, valor_unitario=1, origen=Origen.MANUAL, creado_por="u",
        )
        assert repo.insert(doc)[1] is True


def test_referral_hook_is_idempotent_for_epic_7(scene) -> None:
    db, client_as, patient = scene
    remision = uuid.uuid4()
    first = billing_service.bill_referral(db, patient_id=uuid.UUID(patient["id"]), remision_id=remision)
    second = billing_service.bill_referral(db, patient_id=uuid.UUID(patient["id"]), remision_id=remision)
    assert first.created is True and second.created is False
    (invoice,) = client_as("contable.eps").get(f"{FACTURAS}?servicio=remision").json()
    assert invoice["remision_id"] == str(remision) and invoice["valor"] == tariffs.REMISION
    assert len(_notifications(db, "contable.eps")) == 1


# ---------------------------------------------------------------------------
# Receipt and minimization
# ---------------------------------------------------------------------------


def test_receipt_pdf_has_the_patient_name_resolved_from_postgres(scene) -> None:
    db, client_as, patient = scene
    fid = _manual(client_as("contable.eps"), patient["id"], valor_unitario=120_000).json()["id"]
    client_as("contable.eps").post(f"{FACTURAS}/{fid}/pagar")

    response = client_as("contable.eps").get(f"{FACTURAS}/{fid}/recibo.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    text = response.content.decode("latin-1")
    assert "Zacarias Quimbayaxx" in text
    assert fid in text and "$120.000 COP" in text and "PAGADA" in text
    eps = db.get(Patient, uuid.UUID(patient["id"])).eps_organization_id
    assert db.get(Organization, eps).name in text
    # The admin can download it too.
    assert client_as("admin").get(f"{FACTURAS}/{fid}/recibo.pdf").content.startswith(b"%PDF")


def test_mongo_stores_no_personal_data(scene, mongo_facturas) -> None:
    db, client_as, patient = scene
    _approve(client_as, patient["id"])
    _manual(client_as("contable.eps"), patient["id"])
    assignment = _active_assignment(db)
    client_as("admin").post(f"/api/v1/beds/{assignment.location_id}/release")

    stored = list(mongo_facturas.find())
    assert len(stored) >= 3
    forbidden_keys = {"nombre", "name", "first_name", "last_name", "documento", "document",
                      "document_number", "document_type", "telefono", "phone", "email", "correo",
                      "direccion", "address", "birth_date", "fecha_nacimiento", "paciente_nombre"}
    everything = json.dumps(stored, default=str).lower()
    for doc in stored:
        assert not (set(doc) & forbidden_keys), set(doc)
        assert set(doc) <= {
            "_id", "paciente_id", "eps_id", "ips_id", "servicio", "descripcion", "cantidad",
            "valor_unitario", "valor", "fecha", "estado_pago", "origen", "creado_por",
            "reporte_id", "solicitud_cama_id", "cama_id", "remision_id", "historial",
        }
    for value in [v for k, v in CANARY.items() if k not in {"document_type", "gender"}]:
        assert value.lower() not in everything, value
    for name in ("zacarias", "quimbayaxx"):
        assert name not in everything
