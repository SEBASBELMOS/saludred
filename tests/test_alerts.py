# ruff: noqa: F811
"""Epic 7 -- critical alerts with acknowledgement and escalation (R20), the
"free but not cleaned" bed alert, and the monitor that drives both."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.events.channel import registry
from app.main import app
from app.models import Alert, BedStatusEvent, Location, Organization, Patient
from app.models.enums import (
    AlertKind,
    AlertStatus,
    AuditAction,
    BedStatus,
    LocationType,
)
from app.services import ai_reports, alert_monitor
from app.services import alerts as alerts_service
from tests.reactive_helpers import (
    ALERTS,
    PENDING,
    actors,
    audit_rows,
    inbox,
)
from tests.test_ai_reports import _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

EVERYBODY = (
    "admin", "coordinador.eps", "contable.eps", "operador.norte", "operador.sur",
    "operador.centro", "especialista.norte", "paciente.demo",
)


@pytest.fixture
def scene(db, client_as, ml):
    patient = _create_patient(db, client_as)
    return db, actors(client_as), patient, ml


def _alerts_of(db, patient_id: str) -> list[Alert]:
    return list(db.scalars(select(Alert).where(Alert.patient_id == uuid.UUID(patient_id))))


def _critical(client_as, patient, **body) -> dict:  # noqa: ANN001, ANN003
    response = _analyze(client_as("operador.norte"), patient["id"], **body)
    assert response.status_code == 201, response.text
    return response.json()


def _mine(notes, alert_id) -> list:  # noqa: ANN001
    return [n for n in notes if alert_id in (n.data or {}).get("alerta_ids", [])]


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def test_a_critical_analysis_raises_an_active_alert_for_the_clinician(scene) -> None:
    db, client_as, patient, _ml = scene
    report = _critical(client_as, patient)

    shown = client_as("operador.norte").get(ALERTS).json()
    assert len(shown) == 1
    alert = shown[0]
    assert {"id", "paciente_id", "nivel", "estado", "kind", "mensaje", "created_at", "escalated_at"} <= set(alert)
    assert alert["paciente_id"] == patient["id"] and alert["estado"] == "activa"
    assert alert["nivel"] == "critico" and alert["kind"] == "paciente_critico"
    assert alert["reporte_id"] == report["id"] and alert["escalated_at"] is None
    assert alert["destinatario_id"] == str(_user(db, "operador.norte").id)
    assert "Zacarias" in alert["mensaje"]

    # Delivered to the responsible clinician, with a type that says "alerta".
    (note,) = inbox(db, "operador.norte", "alerta_critica")
    assert "alerta" in note.kind and note.patient_id == uuid.UUID(patient["id"])
    assert note.data["alerta_id"] == alert["id"] and note.data["reporte_id"] == report["id"]
    # Nobody else is told: not the patient, not the accountant, not other IPS, not the admin.
    for username in EVERYBODY:
        if username != "operador.norte":
            assert inbox(db, username, "alerta_critica") == [], username

    # It was raised by the ANALYSIS, not by the approval.
    client_as("operador.norte").post(f"/api/v1/ai-reports/{report['id']}/approve")
    assert len(_alerts_of(db, patient["id"])) == 1


def test_the_alert_creation_is_audited_with_patient_and_report(scene) -> None:
    db, client_as, patient, _ml = scene
    report = _critical(client_as, patient)
    (row,) = audit_rows(db, AuditAction.ALERT_CREATED)
    assert row.metadata_json["patient_id"] == patient["id"]
    assert row.metadata_json["report_id"] == report["id"]
    assert row.metadata_json["nivel"] == "critico" and row.user_id is None  # raised by the system


def test_a_non_critical_analysis_raises_nothing(scene) -> None:
    db, client_as, patient, ml = scene
    ml.probability = 0.20
    report = _critical(client_as, patient)
    assert report["critico"] is False
    client_as("operador.norte").post(f"/api/v1/ai-reports/{report['id']}/approve")
    assert _alerts_of(db, patient["id"]) == []
    assert client_as("operador.norte").get(ALERTS).json() == []
    assert inbox(db, "operador.norte", "alerta_critica") == []
    assert audit_rows(db, AuditAction.ALERT_CREATED) == []


def test_the_level_is_alto_between_the_threshold_and_the_critical_cutoff(scene) -> None:
    _db, client_as, patient, ml = scene
    ml.probability = 0.60  # above the model threshold (0.465), below 0.80
    _critical(client_as, patient)
    assert client_as("operador.norte").get(ALERTS).json()[0]["nivel"] == "alto"


def test_only_one_active_alert_per_patient(scene) -> None:
    """A second critical report is attached to the active alert: no duplicate
    banner and no second notification."""

    db, client_as, patient, _ml = scene
    first = _critical(client_as, patient)
    second = _critical(client_as, patient, triage_level=2)  # same payload would be deduplicated
    assert first["id"] != second["id"]

    (alert,) = _alerts_of(db, patient["id"])
    assert alert.status == AlertStatus.ACTIVE and alert.ai_report_id == uuid.UUID(second["id"])
    assert len(inbox(db, "operador.norte", "alerta_critica")) == 1
    assert len(audit_rows(db, AuditAction.ALERT_CREATED)) == 1
    assert len(client_as("operador.norte").get(f"{ALERTS}?estado=activa").json()) == 1

    # Once acknowledged, the next critical analysis raises a fresh alert.
    assert client_as("operador.norte").post(
        f"{ALERTS}/{alert.id}/acknowledge", json={"nota": "Revisado"}
    ).status_code == 200
    _critical(client_as, patient, triage_level=3)
    states = sorted(a.status.value for a in _alerts_of(db, patient["id"]))
    assert states == ["activa", "reconocida"]


def test_the_database_refuses_a_second_active_alert(scene) -> None:
    """The partial unique index is the last line of defence behind the check."""

    from sqlalchemy.exc import IntegrityError

    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    db.add(
        Alert(
            kind=AlertKind.CRITICAL_PATIENT, nivel="critico", status=AlertStatus.ACTIVE,
            mensaje="duplicada", patient_id=uuid.UUID(patient["id"]),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_without_a_responsible_clinician_the_ips_clinicians_are_told(scene) -> None:
    db, client_as, patient, _ml = scene
    db.get(Patient, uuid.UUID(patient["id"])).responsible_clinician_id = None
    db.commit()
    _critical(client_as, patient)
    for username in ("operador.norte", "especialista.norte"):
        assert len(inbox(db, username, "alerta_critica")) == 1, username
    for username in ("operador.sur", "paciente.demo", "contable.eps", "admin"):
        assert inbox(db, username, "alerta_critica") == [], username
    (alert,) = _alerts_of(db, patient["id"])
    assert alert.recipient_user_id is None


def test_an_inactive_responsible_clinician_falls_back_too(scene) -> None:
    db, client_as, patient, _ml = scene
    _user(db, "operador.norte").is_active = False
    db.get(Patient, uuid.UUID(patient["id"])).responsible_clinician_id = _user(db, "operador.norte").id
    db.commit()
    app_user = "especialista.norte"
    report = ai_reports.request_analysis(
        db, db.get(Patient, uuid.UUID(patient["id"])), actor=_user(db, app_user)
    )
    assert report.is_critical
    assert len(inbox(db, "especialista.norte", "alerta_critica")) == 1
    assert inbox(db, "operador.norte", "alerta_critica") == []  # inactive: never notified


# ---------------------------------------------------------------------------
# Who sees and who acknowledges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("username", ["paciente.demo", "contable.eps"])
def test_patient_and_accountant_neither_see_nor_acknowledge(scene, username) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert_id = _alerts_of(db, patient["id"])[0].id
    client = client_as(username)
    assert client.get(ALERTS).status_code == 403
    assert client.get(f"{ALERTS}/{alert_id}").status_code == 403
    assert client.post(f"{ALERTS}/{alert_id}/acknowledge", json={"nota": "x"}).status_code == 403
    assert _alerts_of(db, patient["id"])[0].status == AlertStatus.ACTIVE


def test_list_scope_by_role(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert_id = str(_alerts_of(db, patient["id"])[0].id)

    def ids(username: str) -> list[str]:
        return [a["id"] for a in client_as(username).get(ALERTS).json()]

    assert ids("operador.norte") == [alert_id]
    assert ids("especialista.norte") == [alert_id]  # a patient of their IPS
    assert ids("admin") == [alert_id]  # the admin reads every alert
    assert ids("operador.sur") == [] and ids("operador.centro") == []  # another IPS
    assert ids("coordinador.eps") == []  # the coordinator handles bed alerts
    assert client_as("operador.sur").get(f"{ALERTS}/{alert_id}").status_code == 404
    assert client_as("coordinador.eps").get(f"{ALERTS}/{alert_id}").status_code == 404
    assert client_as("operador.norte").get(f"{ALERTS}/{alert_id}").status_code == 200
    assert client_as("operador.norte").get(f"{ALERTS}/{uuid.uuid4()}").status_code == 404


def test_a_deleted_patients_alert_leaves_the_list(scene) -> None:
    _db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    assert len(client_as("operador.norte").get(ALERTS).json()) == 1
    client_as("operador.norte").delete(f"/api/v1/patients/{patient['id']}")
    assert client_as("operador.norte").get(ALERTS).json() == []
    assert client_as("operador.norte").get(PENDING).json()["alertas_activas"] == 0


def test_the_specialist_with_a_referral_sees_the_alert_of_the_referred_patient(scene) -> None:
    from tests.reactive_helpers import add_specialist, refer

    db, client_as, patient, _ml = scene
    sur = add_specialist(db, username="especialista.sur", org_code="IPS-SUR")
    _critical(client_as, patient)
    assert client_as("especialista.sur").get(ALERTS).json() == []
    refer(client_as("operador.norte"), patient["id"], sur)
    assert len(client_as("especialista.sur").get(ALERTS).json()) == 1
    assert client_as("especialista.sur").get(PENDING).json()["alertas_activas"] == 1


# ---------------------------------------------------------------------------
# Acknowledge
# ---------------------------------------------------------------------------


def test_acknowledging_needs_a_note_and_closes_the_alert(scene) -> None:
    db, client_as, patient, _ml = scene
    report = _critical(client_as, patient)
    alert_id = str(_alerts_of(db, patient["id"])[0].id)
    medico = client_as("operador.norte")
    url = f"{ALERTS}/{alert_id}/acknowledge"

    assert medico.post(url, json={}).status_code == 422
    assert medico.post(url, json={"nota": ""}).status_code == 422
    assert medico.post(url, json={"nota": "   "}).status_code == 422
    assert medico.post(url, json={"nota": "ok", "extra": 1}).status_code == 422
    assert medico.get(ALERTS).json()[0]["estado"] == "activa"

    done = medico.post(url, json={"nota": "  Paciente valorado, se hospitaliza  "})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["estado"] == "reconocida" and body["id"] == alert_id
    assert body["nota_reconocimiento"] == "Paciente valorado, se hospitaliza"
    assert body["reconocida_por"] == str(_user(db, "operador.norte").id)
    assert body["reconocida_por_nombre"] and body["reconocida_en"]

    assert medico.post(url, json={"nota": "otra vez"}).status_code == 409
    assert [a["id"] for a in medico.get(f"{ALERTS}?estado=reconocida").json()] == [alert_id]
    assert medico.get(f"{ALERTS}?estado=activa").json() == []
    assert medico.get(f"{ALERTS}?estado=nada").status_code == 422

    (row,) = audit_rows(db, AuditAction.ALERT_ACKNOWLEDGED)
    assert row.metadata_json["patient_id"] == patient["id"]
    assert row.metadata_json["report_id"] == report["id"]
    assert row.metadata_json["nota"] == "Paciente valorado, se hospitaliza"
    assert row.user_id == _user(db, "operador.norte").id


def test_the_admin_reads_but_does_not_acknowledge_clinical_alerts(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert_id = _alerts_of(db, patient["id"])[0].id
    denied = client_as("admin").post(f"{ALERTS}/{alert_id}/acknowledge", json={"nota": "x"})
    assert denied.status_code == 403
    assert "administrador" in denied.json()["detail"]
    assert _alerts_of(db, patient["id"])[0].status == AlertStatus.ACTIVE


def test_a_colleague_with_access_may_acknowledge_but_another_ips_may_not(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert_id = _alerts_of(db, patient["id"])[0].id
    url = f"{ALERTS}/{alert_id}/acknowledge"
    assert client_as("operador.sur").post(url, json={"nota": "x"}).status_code == 404
    assert client_as("coordinador.eps").post(url, json={"nota": "x"}).status_code == 404
    done = client_as("especialista.norte").post(url, json={"nota": "Lo tomo yo"})
    assert done.status_code == 200 and done.json()["reconocida_por_nombre"] == "Especialista Clinica Norte"


def test_the_acknowledgement_reaches_the_other_open_screens(scene) -> None:
    """``alerta_critica`` to the clinician, ``alerta_reconocida`` to everybody who
    can see the banner (so it clears); both live, the second never in the inbox."""

    db, client_as, patient, _ml = scene
    medico, colleague = _user(db, "operador.norte"), _user(db, "especialista.norte")
    app.dependency_overrides[get_db] = lambda: db
    url = "/api/v1/events/ws?tipos=alerta_critica,alerta_reconocida&token="
    with TestClient(app) as test_client:
        with (
            test_client.websocket_connect(url + create_access_token(medico.id, medico.role.code.value)) as ws_doctor,
            test_client.websocket_connect(url + create_access_token(colleague.id, colleague.role.code.value)) as ws_colleague,
        ):
            deadline = time.monotonic() + 3
            while registry.connection_count(medico.id) != 1 or registry.connection_count(colleague.id) != 1:
                assert time.monotonic() < deadline, "the sockets never subscribed"
                time.sleep(0.01)
            ai_reports.request_analysis(db, db.get(Patient, uuid.UUID(patient["id"])), actor=medico)
            raised = ws_doctor.receive_json()
            alert = _alerts_of(db, patient["id"])[0]
            alerts_service.acknowledge(db, alert, actor=medico, nota="Visto")
            cleared_doctor = ws_doctor.receive_json()
            cleared_colleague = ws_colleague.receive_json()
    registry.clear()

    assert raised["tipo"] == "alerta_critica" and raised["paciente_id"] == patient["id"]
    assert raised["datos"]["alerta_id"] == str(alert.id) and raised["leida"] is False
    for message in (cleared_doctor, cleared_colleague):
        assert message["tipo"] == "alerta_reconocida" and message["datos"]["alerta_id"] == str(alert.id)
        assert message["datos"]["estado"] == "reconocida"
    # The colleague never got the alert itself (the first thing they see is the clearing).
    assert inbox(db, "especialista.norte", "alerta_critica") == []
    for username in EVERYBODY:
        assert inbox(db, username, "alerta_reconocida") == [], username


# ---------------------------------------------------------------------------
# Escalation (R20.4), with a frozen clock
# ---------------------------------------------------------------------------


def _created_at(alert: Alert) -> datetime:
    moment = alert.created_at
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def test_an_unacknowledged_alert_is_escalated_once(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert = _alerts_of(db, patient["id"])[0]
    born = _created_at(alert)

    assert alerts_service.escalate_overdue(db, now=born + timedelta(minutes=4), minutes=5) == []
    assert inbox(db, "admin", "alerta_escalada") == []

    escalated = alerts_service.escalate_overdue(db, now=born + timedelta(minutes=6), minutes=5)
    assert [a.id for a in escalated] == [alert.id]
    db.refresh(alert)
    assert alert.escalated_at is not None and alert.status == AlertStatus.ACTIVE
    admin_id, colleague_id = _user(db, "admin").id, _user(db, "especialista.norte").id
    assert set(alert.escalated_to) == {str(admin_id), str(colleague_id)}
    assert str(_user(db, "operador.norte").id) not in alert.escalated_to  # already told

    # Admins and the other clinicians of the IPS, with the patient in the message and the data.
    for username in ("admin", "especialista.norte"):
        (note,) = inbox(db, username, "alerta_escalada")
        assert "alerta" in note.kind and "Zacarias" in note.message
        assert note.patient_id == uuid.UUID(patient["id"]) and note.data["alerta_id"] == str(alert.id)
    for username in ("operador.norte", "operador.sur", "paciente.demo", "contable.eps", "coordinador.eps"):
        assert inbox(db, username, "alerta_escalada") == [], username
    (row,) = audit_rows(db, AuditAction.ALERT_ESCALATED)
    assert row.metadata_json["patient_id"] == patient["id"] and row.metadata_json["minutes"] == 5

    # Only once, however long it stays active.
    assert alerts_service.escalate_overdue(db, now=born + timedelta(hours=3), minutes=5) == []
    assert len(inbox(db, "admin", "alerta_escalada")) == 1
    shown = client_as("operador.norte").get(ALERTS).json()[0]
    assert shown["escalated_at"] is not None and set(shown["escalated_to"]) == set(alert.escalated_to)
    # An escalated alert can still be acknowledged.
    assert client_as("operador.norte").post(
        f"{ALERTS}/{alert.id}/acknowledge", json={"nota": "Ya estoy"}
    ).status_code == 200


def test_an_acknowledged_alert_is_never_escalated(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    alert = _alerts_of(db, patient["id"])[0]
    client_as("operador.norte").post(f"{ALERTS}/{alert.id}/acknowledge", json={"nota": "Visto"})
    late = _created_at(alert) + timedelta(hours=2)
    assert alerts_service.escalate_overdue(db, now=late, minutes=5) == []
    assert audit_rows(db, AuditAction.ALERT_ESCALATED) == []


def test_escalation_waits_for_the_configured_minutes(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    born = _created_at(_alerts_of(db, patient["id"])[0])
    assert alerts_service.escalate_overdue(db, now=born + timedelta(minutes=29), minutes=30) == []
    assert len(alerts_service.escalate_overdue(db, now=born + timedelta(minutes=30), minutes=30)) == 1


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------


def test_one_failing_job_does_not_stop_the_other(scene, monkeypatch) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    born = _created_at(_alerts_of(db, patient["id"])[0])

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("test: escalation exploded")

    monkeypatch.setattr(alerts_service, "escalate_overdue", boom)
    summary = alert_monitor.tick(
        db, now=born + timedelta(hours=1), escalation_minutes=5, bed_minutes=60
    )
    assert summary["escaladas"] == 0  # failed, but the bed job still ran
    assert summary["camas_creadas"] >= 0


def test_run_once_uses_a_fresh_session_and_closes_it(scene) -> None:
    db, client_as, patient, _ml = scene
    _critical(client_as, patient)
    born = _created_at(_alerts_of(db, patient["id"])[0])
    opened: list = []
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def tracked():  # noqa: ANN202
        session = factory()
        opened.append(session)
        return session

    settings = SimpleNamespace(alert_escalation_minutes=5, bed_cleaning_alert_minutes=60)
    summary = alert_monitor.run_once(settings, session_factory=tracked, now=born + timedelta(minutes=10))
    assert summary["escaladas"] == 1 and len(opened) == 1
    assert opened[0] is not db
    db.expire_all()
    assert _alerts_of(db, patient["id"])[0].escalated_at is not None


def test_the_loop_survives_errors_and_stops_on_cancel(monkeypatch) -> None:
    calls: list[int] = []

    def flaky(_settings, **_kwargs):  # noqa: ANN001, ANN003
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("test: first tick crashes")
        return {}

    monkeypatch.setattr(alert_monitor, "run_once", flaky)
    settings = SimpleNamespace(
        alert_monitor_interval_seconds=0.01, alert_escalation_minutes=5, bed_cleaning_alert_minutes=60
    )

    async def scenario() -> int:
        task = asyncio.create_task(alert_monitor.monitor_loop(settings))
        deadline = time.monotonic() + 3
        while len(calls) < 3:
            assert time.monotonic() < deadline, "the loop died after the first error"
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return len(calls)

    assert asyncio.run(scenario()) >= 3


def test_the_lifespan_starts_and_stops_the_monitor_only_when_enabled(monkeypatch) -> None:
    state = {"started": 0, "cancelled": 0}

    async def fake_loop(_settings, **_kwargs):  # noqa: ANN001, ANN003
        state["started"] += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise

    monkeypatch.setattr(alert_monitor, "monitor_loop", fake_loop)
    with TestClient(app):  # the suite runs with ALERT_ESCALATION_ENABLED=false
        time.sleep(0.1)
    assert state == {"started": 0, "cancelled": 0}

    monkeypatch.setattr(get_settings(), "alert_escalation_enabled", True)
    with TestClient(app):
        deadline = time.monotonic() + 3
        while state["started"] == 0:
            assert time.monotonic() < deadline, "the monitor was not started"
            time.sleep(0.01)
    assert state == {"started": 1, "cancelled": 1}


# ---------------------------------------------------------------------------
# The bed alert: free but not cleaned
# ---------------------------------------------------------------------------

# A frozen "now" well after everything the seed wrote (it stamps its rows with the
# real clock), so a bed put in CLEANING at ``NOW - n hours`` is always its latest event.
NOW = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)


def _bed_in_cleaning(db, org_code: str, *, since: datetime) -> Location:
    """A bed of the IPS that entered CLEANING at ``since`` with no evidence."""

    org = db.scalar(select(Organization).where(Organization.code == org_code))
    bed = db.scalar(
        select(Location).where(
            Location.organization_id == org.id,
            Location.location_type == LocationType.BED,
            Location.status == BedStatus.AVAILABLE,
        )
    )
    bed.status = BedStatus.CLEANING
    db.add(
        BedStatusEvent(
            location_id=bed.id, previous_status=BedStatus.AVAILABLE,
            new_status=BedStatus.CLEANING, event_at=since, reason="test",
        )
    )
    db.commit()
    return bed


def _bed_alert(db, bed: Location) -> Alert | None:
    return db.scalar(select(Alert).where(Alert.bed_id == bed.id))


def _active_bed_alerts(db, org_code: str) -> int:
    org = db.scalar(select(Organization).where(Organization.code == org_code))
    return len(
        list(
            db.scalars(
                select(Alert).where(
                    Alert.kind == AlertKind.BED_NOT_CLEANED,
                    Alert.status == AlertStatus.ACTIVE,
                    Alert.organization_id == org.id,
                )
            )
        )
    )


def test_a_bed_alert_is_raised_once_the_cleaning_is_overdue(db) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(minutes=10))
    # Ten minutes in cleaning is normal: nothing yet.
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    assert _bed_alert(db, bed) is None
    # Frozen clock, 70 minutes later: now it is "losing time".
    result = alerts_service.sync_bed_alerts(db, now=NOW + timedelta(minutes=60), minutes=60)
    assert result["creadas"] >= 1
    alert = _bed_alert(db, bed)
    assert alert.kind == AlertKind.BED_NOT_CLEANED and alert.status == AlertStatus.ACTIVE
    assert alert.patient_id is None and alert.recipient_user_id is None
    assert alert.organization_id == bed.organization_id and alert.nivel == "alto"
    assert "esta libre, pero no ha sido limpiada" in alert.mensaje and bed.code in alert.mensaje
    assert alert.datos["cama"] == bed.code and alert.datos["minutos_en_limpieza"] >= 60
    (row,) = [r for r in audit_rows(db, AuditAction.ALERT_CREATED) if r.entity_id == alert.id]
    assert row.metadata_json["bed_id"] == str(bed.id) and row.user_id is None


def test_the_bed_alert_is_not_duplicated_while_active(db) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    second = alerts_service.sync_bed_alerts(db, now=NOW + timedelta(minutes=30), minutes=60)
    assert second["creadas"] == 0
    assert len(list(db.scalars(select(Alert).where(Alert.bed_id == bed.id)))) == 1


def test_the_bed_alert_reaches_the_coordinator_and_the_ips_medicos_only(db, client_as) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    mine = str(_bed_alert(db, bed).id)

    for username in ("coordinador.eps", "operador.norte"):
        notes = _mine(inbox(db, username, "alerta_cama_sin_limpiar"), mine)
        assert len(notes) == 1 and "alerta" in notes[0].kind, username
        assert notes[0].patient_id is None
    for username in ("operador.sur", "operador.centro", "especialista.norte", "admin",
                     "paciente.demo", "contable.eps"):
        assert _mine(inbox(db, username), mine) == [], username

    # Who SEES it in the list.
    def ids(username: str) -> list[str]:
        return [a["id"] for a in client_as(username).get(ALERTS).json()]

    for username in ("coordinador.eps", "operador.norte", "admin"):
        assert mine in ids(username), username
    for username in ("operador.sur", "operador.centro", "especialista.norte"):
        assert mine not in ids(username), username
    for username in ("paciente.demo", "contable.eps"):
        assert client_as(username).get(ALERTS).status_code == 403


def test_a_bed_alert_counts_only_for_those_it_is_addressed_to(db, client_as) -> None:
    _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)

    def active(username: str) -> int:
        return client_as(username).get(PENDING).json()["alertas_activas"]

    assert active("operador.norte") == _active_bed_alerts(db, "IPS-NORTE") >= 1
    assert active("operador.sur") == _active_bed_alerts(db, "IPS-SUR")
    assert active("especialista.norte") == 0  # a specialist is not an addressee
    assert active("paciente.demo") == 0 and active("contable.eps") == 0
    every_active = len(list(db.scalars(select(Alert).where(Alert.status == AlertStatus.ACTIVE))))
    assert active("admin") == every_active
    assert active("coordinador.eps") == every_active  # all of them are bed alerts here


def test_who_acknowledges_a_bed_alert(db, client_as) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    alert_id = _bed_alert(db, bed).id
    url = f"{ALERTS}/{alert_id}/acknowledge"
    body = {"nota": "Limpieza en curso"}

    assert client_as("paciente.demo").post(url, json=body).status_code == 403
    assert client_as("contable.eps").post(url, json=body).status_code == 403
    assert client_as("operador.sur").post(url, json=body).status_code == 404  # not theirs
    assert client_as("especialista.norte").post(url, json=body).status_code == 404
    assert client_as("operador.norte").post(url, json={}).status_code == 422  # note required
    done = client_as("coordinador.eps").post(url, json=body)
    assert done.status_code == 200 and done.json()["estado"] == "reconocida"
    assert client_as("admin").post(url, json=body).status_code == 409
    assert _bed_alert(db, bed).acknowledged_by == _user(db, "coordinador.eps").id


def test_admin_and_the_ips_medico_can_also_acknowledge_a_bed_alert(db, client_as) -> None:
    first = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    admin_done = client_as("admin").post(
        f"{ALERTS}/{_bed_alert(db, first).id}/acknowledge", json={"nota": "Aviso a limpieza"}
    )
    assert admin_done.status_code == 200, admin_done.text

    second = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=4))
    alerts_service.sync_bed_alerts(db, now=NOW + timedelta(minutes=1), minutes=60)
    medico_done = client_as("operador.norte").post(
        f"{ALERTS}/{_bed_alert(db, second).id}/acknowledge", json={"nota": "Ya la reviso"}
    )
    assert medico_done.status_code == 200, medico_done.text


def test_a_bed_alert_resolves_itself_when_the_bed_is_no_longer_overdue(db) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    alert = _bed_alert(db, bed)
    assert alert.status == AlertStatus.ACTIVE

    bed.status = BedStatus.AVAILABLE  # the cleaning was approved and the bed released
    db.commit()
    result = alerts_service.sync_bed_alerts(db, now=NOW + timedelta(minutes=5), minutes=60)
    assert result["resueltas"] >= 1
    db.refresh(alert)
    assert alert.status == AlertStatus.ACKNOWLEDGED and alert.acknowledged_by is None
    assert alert.nota_reconocimiento == alerts_service.AUTO_RESOLVED_NOTE
    auto = [
        r for r in audit_rows(db, AuditAction.ALERT_ACKNOWLEDGED)
        if r.entity_id == alert.id
    ]
    assert len(auto) == 1 and auto[0].metadata_json["auto"] is True and auto[0].user_id is None
    # It left the active list and the bed can alert again later.
    bed.status = BedStatus.CLEANING
    db.add(
        BedStatusEvent(
            location_id=bed.id, previous_status=BedStatus.AVAILABLE,
            new_status=BedStatus.CLEANING, event_at=NOW + timedelta(minutes=6), reason="again",
        )
    )
    db.commit()
    alerts_service.sync_bed_alerts(db, now=NOW + timedelta(hours=2), minutes=60)
    states = sorted(a.status.value for a in db.scalars(select(Alert).where(Alert.bed_id == bed.id)))
    assert states == ["activa", "reconocida"]


def test_a_bed_alert_is_escalated_to_admins_and_the_other_clinicians(db) -> None:
    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    alert = _bed_alert(db, bed)
    escalated = alerts_service.escalate_overdue(
        db, now=_created_at(alert) + timedelta(minutes=6), minutes=5
    )
    assert alert.id in [a.id for a in escalated]
    db.refresh(alert)
    admin_id, specialist_id = _user(db, "admin").id, _user(db, "especialista.norte").id
    assert {str(admin_id), str(specialist_id)} <= set(alert.escalated_to)
    # The coordinator and the IPS medicos were already told.
    assert str(_user(db, "coordinador.eps").id) not in alert.escalated_to
    assert str(_user(db, "operador.norte").id) not in alert.escalated_to
    for username in ("admin", "especialista.norte"):
        assert len(_mine(inbox(db, username, "alerta_escalada"), str(alert.id))) == 1, username
    for username in ("paciente.demo", "contable.eps", "operador.sur"):
        assert _mine(inbox(db, username, "alerta_escalada"), str(alert.id)) == [], username


def test_a_backlog_of_beds_is_announced_once_per_ips(db) -> None:
    """Several overdue beds at the same IPS must not flood the inboxes: one event."""

    first = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    second = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=4))
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    ids = {str(_bed_alert(db, first).id), str(_bed_alert(db, second).id)}
    notes = [n for n in inbox(db, "operador.norte", "alerta_cama_sin_limpiar") if ids & set(n.data["alerta_ids"])]
    assert len(notes) == 1 and ids <= set(notes[0].data["alerta_ids"])
    assert "camas estan libres, pero no han sido limpiadas" in notes[0].message


def test_overdue_beds_honour_the_injected_clock_and_default_to_the_current_time(db) -> None:
    """``now`` is optional: the endpoint relies on the default."""

    from app.services import cleaning

    bed = _bed_in_cleaning(db, "IPS-NORTE", since=datetime.now(timezone.utc) - timedelta(hours=2))
    before = datetime.now(timezone.utc)
    default = {item["location_id"] for item in cleaning.beds_cleaning_overdue(db, 0)}
    explicit = {item["location_id"] for item in cleaning.beds_cleaning_overdue(db, 0, now=before)}
    assert bed.id in default and default == explicit
    # A clock set before everything happened finds nothing overdue.
    assert cleaning.beds_cleaning_overdue(db, 0, now=datetime(2000, 1, 1, tzinfo=timezone.utc)) == []


def test_an_inactive_clinician_is_not_notified_of_a_bed_alert(db) -> None:
    """Inactive accounts are skipped by the notification reactor."""

    bed = _bed_in_cleaning(db, "IPS-NORTE", since=NOW - timedelta(hours=3))
    _user(db, "operador.norte").is_active = False
    db.commit()
    alerts_service.sync_bed_alerts(db, now=NOW, minutes=60)
    assert _mine(inbox(db, "operador.norte"), str(_bed_alert(db, bed).id)) == []
