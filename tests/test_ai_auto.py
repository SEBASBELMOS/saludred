# ruff: noqa: F811
"""Automatic AI analysis, the idempotent manual route and the mandatory review text.

The feature is switched on per test (``conftest`` forces ``AI_AUTO_ANALYSIS`` to
false) and the background worker runs inline with its own session, so every
assertion is deterministic. The AI service is the ``FakeMl`` of the R10 tests.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.events.channel import registry
from app.main import app
from app.models import AiReport, Alert, AuditLog, Encounter, Observation, Patient, User
from app.models.enums import AiReportOrigin, AiReportStatus, AuditAction
from app.schemas.observation import ObservationCreate
from app.services import ai_auto, ai_reports, alert_monitor, ml_client, patient_locks
from app.services import observations as observations_service
from app.services.errors import ConflictError
from tests.fhir_helpers import HOOK, FakeFhir, glucose, hook_headers, sync_patient_to_fake
from tests.reactive_helpers import inbox
from tests.test_ai_reports import VITALS, FakeMl, _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

CORE = VITALS[:6]  # temperature, heart rate, respiratory rate, SpO2, systolic, diastolic
MESSAGE = "Nuevo análisis de IA pendiente de firma: Zacarias Quimbayaxx"
NOTE = "Concuerda con la clinica y el examen fisico"


@pytest.fixture
def auto(db, client_as, ml, monkeypatch):
    """Automatic analysis ON, run inline, with its own sessions."""

    monkeypatch.setattr(get_settings(), "ai_auto_analysis", True)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    ai_auto.configure(session_factory=factory, inline=True)
    # The seeded dataset was "recorded" a moment ago; in a real database it is old,
    # and the sweep only looks at recent activity.
    db.execute(update(Observation).values(created_at=datetime.now(timezone.utc) - timedelta(days=2)))
    db.commit()
    patient = _create_patient(db, client_as, with_vitals=False)
    yield db, client_as, patient, ml
    ai_auto.configure()


def _vital_body(patient: dict, vital: tuple) -> dict:
    code, display, value, unit = vital
    return {
        "patient_id": patient["id"],
        "encounter_id": patient["encounter_id"],
        "code": code,
        "display": display,
        "value_numeric": value,
        "unit": unit,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _post_vital(client, patient: dict, vital: tuple) -> None:  # noqa: ANN001
    response = client.post("/api/v1/observations", json=_vital_body(patient, vital))
    assert response.status_code == 201, response.text


def _reports(db) -> list[AiReport]:  # noqa: ANN001
    db.expire_all()
    return list(db.scalars(select(AiReport)))


def _tick(db, *, minutes: float = 0) -> dict:  # noqa: ANN001
    return alert_monitor.tick(
        db,
        now=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        escalation_minutes=5,
        bed_minutes=60,
    )


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def test_the_report_appears_when_the_last_core_vital_is_recorded(auto) -> None:
    db, client_as, patient, ml = auto
    doctor = client_as("operador.norte")
    for vital in CORE[:-1]:
        _post_vital(doctor, patient, vital)
        assert _reports(db) == [], "observations arrive one by one: no analysis on a partial set"
    assert ml.criticality_calls == []

    _post_vital(doctor, patient, CORE[-1])
    (report,) = _reports(db)
    assert report.origin == AiReportOrigin.AUTOMATIC
    assert report.status == AiReportStatus.PENDING
    assert report.requested_by is None
    assert report.encounter_id == uuid.UUID(patient["encounter_id"])
    assert set(ml.criticality_calls[0]) >= {
        "temperature_c", "heart_rate", "resp_rate", "o2sat", "sbp", "dbp"
    }

    # The API tells the truth about it.
    body = doctor.get(f"/api/v1/ai-reports?patient_id={patient['id']}").json()
    assert [(r["id"], r["origen"], r["requested_by"]) for r in body] == [
        (str(report.id), "automatico", None)
    ]

    # Audited as AI_ANALYSIS, by the system, with the origin in its metadata.
    entry = db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.AI_ANALYSIS)).one()
    assert entry.user_id is None
    assert entry.metadata_json["origen"] == "automatico"
    assert entry.metadata_json["report_id"] == str(report.id)


def test_reporte_generado_still_fires_the_critical_alert_and_the_counters(auto) -> None:
    db, client_as, patient, _ml = auto
    doctor = client_as("operador.norte")
    before = doctor.get("/api/v1/pendientes").json()
    for vital in CORE:
        _post_vital(doctor, patient, vital)
    after = doctor.get("/api/v1/pendientes").json()
    assert after["reportes_por_revisar"] == before["reportes_por_revisar"] + 1
    assert after["alertas_activas"] == before["alertas_activas"] + 1  # 0.91 is critical
    db.expire_all()
    assert len(list(db.scalars(select(Alert).where(Alert.patient_id == uuid.UUID(patient["id"]))))) == 1


def test_a_seventh_observation_or_a_repeated_vital_makes_no_second_report(auto) -> None:
    db, client_as, patient, ml = auto
    doctor = client_as("operador.norte")
    for vital in CORE:
        _post_vital(doctor, patient, vital)
    _post_vital(doctor, patient, VITALS[6])  # glucose
    _post_vital(doctor, patient, CORE[1])  # heart rate again
    assert len(_reports(db)) == 1
    assert len(ml.criticality_calls) == 1


def test_the_feature_switch_turns_everything_off(auto, monkeypatch) -> None:
    db, client_as, patient, ml = auto
    monkeypatch.setattr(get_settings(), "ai_auto_analysis", False)
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    assert _reports(db) == [] and ml.criticality_calls == []
    assert _tick(db, minutes=10)["ia_automaticos"] == 0


# ---------------------------------------------------------------------------
# Fallback through the monitor tick
# ---------------------------------------------------------------------------


def test_partial_vitals_wait_for_the_grace_period_then_are_analyzed(auto) -> None:
    db, client_as, patient, ml = auto
    doctor = client_as("operador.norte")
    for vital in (CORE[1], CORE[2], CORE[3]):  # heart rate, respiratory rate, SpO2
        _post_vital(doctor, patient, vital)
    assert _reports(db) == []

    # Right now the grace period (120 s) has not passed: the tick leaves it alone.
    assert _tick(db)["ia_automaticos"] == 0
    assert _reports(db) == []

    # Frozen clock, three minutes later: analyzed with what there is.
    assert _tick(db, minutes=3)["ia_automaticos"] == 1
    (report,) = _reports(db)
    assert report.origin == AiReportOrigin.AUTOMATIC and report.requested_by is None
    sent = ml.criticality_calls[0]
    assert {"heart_rate", "resp_rate", "o2sat"} <= set(sent)
    assert not {"temperature_c", "sbp", "dbp"} & set(sent)  # nothing is invented
    assert "acuity" in report.model_response["imputed"]  # the imputed variables travel in the report

    # Once is enough: the next ticks and a late vital change nothing.
    assert _tick(db, minutes=6)["ia_automaticos"] == 0
    _post_vital(doctor, patient, CORE[0])
    assert len(_reports(db)) == 1


def test_the_fallback_needs_the_minimum_usable_variables(auto) -> None:
    db, _client_as, patient, ml = auto
    # age + sex only: below the floor of three variables (and no vital to anchor it).
    assert _tick(db, minutes=10)["ia_automaticos"] == 0
    assert ml.criticality_calls == []


def test_the_fallback_ignores_old_encounters_so_it_never_backfills(auto) -> None:
    db, client_as, patient, ml = auto
    for vital in (CORE[1], CORE[2], CORE[3]):
        _post_vital(client_as("operador.norte"), patient, vital)
    assert _tick(db, minutes=61 + 2)["ia_automaticos"] == 0  # outside the 60 min look-back
    assert _reports(db) == [] and ml.criticality_calls == []


def test_a_closed_encounter_is_not_analyzed(auto) -> None:
    db, client_as, patient, _ml = auto
    for vital in (CORE[1], CORE[2], CORE[3]):
        _post_vital(client_as("operador.norte"), patient, vital)
    encounter = db.get(Encounter, uuid.UUID(patient["encounter_id"]))
    encounter.ended_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    assert _tick(db, minutes=3)["ia_automaticos"] == 0
    assert _reports(db) == []


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_the_database_allows_one_automatic_report_per_encounter(auto) -> None:
    db, client_as, patient, ml = auto
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    (first,) = _reports(db)

    # A second writer that lost the race is refused by the partial UNIQUE index.
    with pytest.raises(ConflictError):
        ai_reports.store_report(
            db,
            db.get(Patient, uuid.UUID(patient["id"])),
            payload=dict(first.payload_sent),
            answer=ml.predict_criticality(dict(first.payload_sent)),
            actor=None,
            origin=AiReportOrigin.AUTOMATIC,
            encounter_id=first.encounter_id,
        )
    assert len(_reports(db)) == 1

    # Manual reports are not limited: the guarantee is only for the automatic one.
    manual = ai_reports.store_report(
        db,
        db.get(Patient, uuid.UUID(patient["id"])),
        payload={**first.payload_sent, "acuity": 2},
        answer=ml.predict_criticality(dict(first.payload_sent)),
        actor=_user(db, "operador.norte"),
        origin=AiReportOrigin.MANUAL,
    )
    assert manual.origin == AiReportOrigin.MANUAL and len(_reports(db)) == 2


def test_a_claimed_encounter_is_skipped(auto) -> None:
    _db, _client_as, patient, ml = auto
    encounter_id = uuid.UUID(patient["encounter_id"])
    assert ai_auto._claim(encounter_id) is True
    try:
        assert ai_auto._claim(encounter_id) is False  # a second worker backs off
        ai_auto.run(encounter_id, allow_partial=True)
        assert ml.criticality_calls == []
    finally:
        ai_auto._release(encounter_id)


def test_the_postgres_index_is_partial_and_unique() -> None:
    index = next(i for i in AiReport.__table__.indexes if i.name == "uq_ai_reports_auto_per_encounter")
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE INDEX uq_ai_reports_auto_per_encounter" in ddl
    assert "(encounter_id, kind)" in ddl and "WHERE origin = 'automatico'" in ddl


def test_the_sweep_query_compiles_for_postgres() -> None:
    now = datetime.now(timezone.utc)
    text = str(
        ai_auto.due_encounters_stmt(cutoff=now, since=now - timedelta(hours=1)).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "NOT (EXISTS" in text or "NOT EXISTS" in text
    assert "GROUP BY encounters.id" in text and "HAVING max(observations.created_at) <=" in text
    assert "encounters.ended_at IS NULL" in text and "patients.deleted_at IS NULL" in text
    assert "ai_reports.created_at >= usable.last_usable" in text  # covered encounters are skipped
    assert "LIMIT" in text


def _quiet_encounter(db, client_as, n: int) -> dict:  # noqa: ANN001
    """A patient with an open encounter and three vitals (enough for the fallback, not for the trigger)."""

    client = client_as("operador.norte")
    org = _user(db, "operador.norte").organization_id
    eps = db.scalars(select(Patient)).first().eps_organization_id
    created = client.post(
        "/api/v1/patients",
        json={
            "document_type": "CC", "document_number": f"7100{n:06d}", "first_name": f"Quieto{n}",
            "last_name": "Barrido", "birth_date": "1961-03-09", "gender": "male",
            "eps_organization_id": str(eps),
        },
    )
    assert created.status_code == 201, created.text
    patient = created.json()
    encounter = client.post(
        "/api/v1/encounters",
        json={
            "patient_id": patient["id"], "organization_id": str(org), "encounter_class": "EMER",
            "status": "in-progress", "priority": "URGENT", "reason_text": "Observacion",
            "started_at": datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert encounter.status_code == 201, encounter.text
    patient["encounter_id"] = encounter.json()["id"]
    for vital in (CORE[1], CORE[2], CORE[3]):
        _post_vital(client, patient, vital)
    return patient


def test_encounters_already_covered_by_a_report_do_not_starve_the_ones_due(auto) -> None:
    db, client_as, _patient, ml = auto
    covered = [_quiet_encounter(db, client_as, n) for n in range(ai_auto.MAX_PER_SWEEP)]
    doctor = client_as("operador.norte")
    for i, patient in enumerate(covered):
        # Manual report for the first half, signed for the rest: neither is an automatic one.
        report = _analyze(doctor, patient["id"]).json()
        if i % 2:
            assert doctor.post(f"/api/v1/ai-reports/{report['id']}/approve").status_code == 200
    due = _quiet_encounter(db, client_as, 99)  # the newest: last in the ORDER BY
    calls_before = len(ml.criticality_calls)
    assert calls_before == ai_auto.MAX_PER_SWEEP

    now = datetime.now(timezone.utc) + timedelta(minutes=3)
    cutoff, since = now - timedelta(seconds=120), now - timedelta(minutes=60)
    assert list(db.scalars(ai_auto.due_encounters_stmt(cutoff=cutoff, since=since))) == [
        uuid.UUID(due["encounter_id"])
    ]

    # One sweep reaches the encounter that is really due.
    assert _tick(db, minutes=3)["ia_automaticos"] == 1
    assert len(ml.criticality_calls) == calls_before + 1
    assert ai_auto._has_automatic_report(db, uuid.UUID(due["encounter_id"]))

    # No repeated work: nothing is due any more and further ticks call nothing.
    assert list(db.scalars(ai_auto.due_encounters_stmt(cutoff=cutoff, since=since))) == []
    assert _tick(db, minutes=4)["ia_automaticos"] == 0
    assert len(ml.criticality_calls) == calls_before + 1


# ---------------------------------------------------------------------------
# FHIR
# ---------------------------------------------------------------------------


def _fhir_vital(patient_ref: str, obs_id: str, vital: tuple) -> dict:
    code, display, value, unit = vital
    resource = glucose(patient_ref, obs_id=obs_id, value=value)
    resource["code"]["coding"][0].update(code=code, display=display)
    resource["valueQuantity"].update(unit=unit, code=unit)
    return resource


def test_observations_arriving_through_fhir_count_too(auto) -> None:
    db, client_as, patient, ml = auto
    fhir_id = sync_patient_to_fake(db, FakeFhir(), uuid.UUID(patient["id"]), _user(db, "admin"))
    doctor = client_as("operador.norte")
    for vital in CORE[:3]:
        _post_vital(doctor, patient, vital)
    admin = client_as("admin")
    for index, vital in enumerate(CORE[3:]):
        assert _reports(db) == []
        resource = _fhir_vital(f"Patient/{fhir_id}", f"obs-vital-{index}", vital)
        response = admin.put(f"{HOOK}/Observation/obs-vital-{index}", json=resource, headers=hook_headers())
        assert response.status_code == 200, response.text
    (report,) = _reports(db)
    assert report.origin == AiReportOrigin.AUTOMATIC
    assert report.encounter_id == uuid.UUID(patient["encounter_id"])
    assert len(ml.criticality_calls) == 1


# ---------------------------------------------------------------------------
# AI service down
# ---------------------------------------------------------------------------


def test_ml_down_writes_nothing_and_the_next_observation_retries(auto) -> None:
    db, client_as, patient, _ml = auto
    ml_client.set_client(ml_client_down())
    doctor = client_as("operador.norte")
    for vital in CORE:
        _post_vital(doctor, patient, vital)  # the clinical writes never fail
    assert _reports(db) == []
    assert db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.AI_ANALYSIS)).first() is None

    fake = FakeMl()
    ml_client.set_client(fake)
    _post_vital(doctor, patient, VITALS[6])  # the next observation of the encounter
    (report,) = _reports(db)
    assert report.origin == AiReportOrigin.AUTOMATIC and len(fake.criticality_calls) == 1


def test_ml_down_is_retried_by_the_monitor_tick(auto) -> None:
    db, client_as, patient, _ml = auto
    ml_client.set_client(ml_client_down())
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    assert _tick(db, minutes=3)["ia_automaticos"] == 0 and _reports(db) == []

    ml_client.set_client(FakeMl())
    assert _tick(db, minutes=3)["ia_automaticos"] == 1
    assert len(_reports(db)) == 1


def ml_client_down():  # noqa: ANN201
    from tests.conftest import _DownMlClient

    return _DownMlClient()


# ---------------------------------------------------------------------------
# Who is told
# ---------------------------------------------------------------------------


def test_the_responsible_clinician_is_told_and_nobody_else(auto) -> None:
    db, client_as, patient, _ml = auto
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    assert [n.message for n in inbox(db, "operador.norte", "analisis_ia_pendiente")] == [MESSAGE]
    note = inbox(db, "operador.norte", "analisis_ia_pendiente")[0]
    assert note.patient_id == uuid.UUID(patient["id"]) and note.data["origen"] == "automatico"
    # Not the colleague (the patient has a responsible clinician), not another IPS,
    # and never the patient or the accountant.
    for username in ("especialista.norte", "operador.sur", "paciente.demo", "contable.eps", "coordinador.eps"):
        assert inbox(db, username, "analisis_ia_pendiente") == [], username
    for username in ("paciente.demo", "contable.eps"):
        assert inbox(db, username, "alerta_critica") == [], username


def test_without_a_responsible_clinician_the_ips_clinicians_are_told(auto) -> None:
    db, client_as, patient, _ml = auto
    db.get(Patient, uuid.UUID(patient["id"])).responsible_clinician_id = None
    db.commit()
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    for username in ("operador.norte", "especialista.norte"):
        assert len(inbox(db, username, "analisis_ia_pendiente")) == 1, username
    for username in ("operador.sur", "paciente.demo", "contable.eps"):
        assert inbox(db, username, "analisis_ia_pendiente") == [], username


def test_the_notification_reaches_the_live_channel(auto) -> None:
    db, client_as, patient, _ml = auto
    doctor_client = client_as("operador.norte")
    for vital in CORE[:-1]:
        _post_vital(doctor_client, patient, vital)
    medico = _user(db, "operador.norte")
    app.dependency_overrides[get_db] = lambda: db
    url = "/api/v1/events/ws?tipos=analisis_ia_pendiente&token="
    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            url + create_access_token(medico.id, medico.role.code.value)
        ) as socket:
            deadline = time.monotonic() + 3
            while registry.connection_count(medico.id) != 1:
                assert time.monotonic() < deadline, "the socket never subscribed"
                time.sleep(0.01)
            last = CORE[-1]
            observations_service.create_observation(
                db, ObservationCreate(**_vital_body(patient, last)), actor=medico
            )
            event = socket.receive_json()
    registry.clear()
    assert event["tipo"] == "analisis_ia_pendiente"
    assert event["mensaje"] == MESSAGE
    assert event["paciente_id"] == patient["id"]
    assert event["datos"]["origen"] == "automatico"


# ---------------------------------------------------------------------------
# The manual route stays, and it is idempotent
# ---------------------------------------------------------------------------


def _with_vitals(db, client_as):  # noqa: ANN001, ANN202
    patient = _create_patient(db, client_as)  # six vitals + glucose, automation OFF
    return patient


def test_asking_again_returns_the_same_pending_report(db, client_as, ml) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    first = _analyze(doctor, patient["id"])
    again = _analyze(doctor, patient["id"])
    assert first.status_code == again.status_code == 201
    assert first.json()["estado"] == "pendiente"
    assert again.json() == first.json()
    assert first.json()["origen"] == "manual"
    assert len(_reports(db)) == 1 and len(ml.criticality_calls) == 1
    audits = db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.AI_ANALYSIS)).all()
    assert len(audits) == 1


def test_a_different_payload_or_a_reviewed_report_creates_a_new_one(db, client_as, ml) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    first = _analyze(doctor, patient["id"]).json()
    other = _analyze(doctor, patient["id"], triage_level=2).json()
    assert other["id"] != first["id"]
    assert _analyze(doctor, patient["id"], triage_level=2).json()["id"] == other["id"]

    doctor.post(f"/api/v1/ai-reports/{first['id']}/reject", json={"motivo": "no concuerda"})
    renewed = _analyze(doctor, patient["id"]).json()
    assert renewed["id"] not in {first["id"], other["id"]}
    assert len(_reports(db)) == 3

    # New vitals change the payload: the pending report no longer matches.
    doctor.post(
        "/api/v1/observations",
        json=_vital_body(patient, ("8867-4", "Heart rate", 131, "/min")),
    )
    assert _analyze(doctor, patient["id"]).json()["id"] != renewed["id"]


def test_the_route_returns_the_waiting_automatic_report_without_calling_the_model(auto) -> None:
    db, client_as, patient, ml = auto
    doctor = client_as("operador.norte")
    for vital in CORE:
        _post_vital(doctor, patient, vital)
    (report,) = _reports(db)

    response = _analyze(doctor, patient["id"])
    assert response.status_code == 201
    assert response.json()["id"] == str(report.id) and response.json()["origen"] == "automatico"
    assert len(_reports(db)) == 1 and len(ml.criticality_calls) == 1

    # Even with the AI service down: nothing to compute, the report already exists.
    ml_client.set_client(ml_client_down())
    assert _analyze(doctor, patient["id"]).json()["id"] == str(report.id)


# ---------------------------------------------------------------------------
# Mandatory clinical judgment (REQUIRE_REVIEW_NOTE)
# ---------------------------------------------------------------------------


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(get_settings(), "require_review_note", True)


def test_the_api_stays_tolerant_by_default(db, client_as, ml) -> None:
    assert get_settings().require_review_note is False
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    assert doctor.post(f"/api/v1/ai-reports/{report}/approve", json={}).status_code == 200


def test_approving_without_a_judgment_text_is_a_422_when_required(db, client_as, ml, strict) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    for body in (None, {}, {"observaciones": ""}, {"observaciones": "   "}, {"observaciones": "corto"}):
        response = doctor.post(f"/api/v1/ai-reports/{report}/approve", json=body)
        assert response.status_code == 422, body
        assert "concepto clinico" in response.json()["detail"]
        assert "10 caracteres" in response.json()["detail"]
    assert doctor.get(f"/api/v1/ai-reports/{report}").json()["estado"] == "pendiente"

    ok = doctor.post(f"/api/v1/ai-reports/{report}/approve", json={"observaciones": NOTE})
    assert ok.status_code == 200 and ok.json()["observaciones"] == NOTE


def test_rejecting_needs_the_judgment_text_too_when_required(db, client_as, ml, strict) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    short = doctor.post(f"/api/v1/ai-reports/{report}/reject", json={"motivo": "no aplica"})
    assert short.status_code == 422 and "concepto clinico" in short.json()["detail"]
    ok = doctor.post(
        f"/api/v1/ai-reports/{report}/reject", json={"motivo": "No coincide con el examen fisico"}
    )
    assert ok.status_code == 200 and ok.json()["motivo_rechazo"] == "No coincide con el examen fisico"


def test_an_already_reviewed_report_is_still_a_409_in_strict_mode(db, client_as, ml, strict) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    doctor.post(f"/api/v1/ai-reports/{report}/approve", json={"observaciones": NOTE})
    assert doctor.post(f"/api/v1/ai-reports/{report}/approve", json={}).status_code == 409


def test_the_judgment_text_is_in_the_record_json_and_the_pdf(db, client_as, ml, strict) -> None:
    patient = _with_vitals(db, client_as)
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    signed = doctor.post(f"/api/v1/ai-reports/{report}/approve", json={"observaciones": NOTE}).json()

    record = doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record").json()
    (item,) = record["reportes_ia"]
    assert item["observaciones"] == NOTE
    assert item["firmado_por"] == signed["firmado_por"] and item["reviewed_at"]

    pdf = doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record.pdf")
    assert pdf.status_code == 200
    assert b"Concepto clinico: " + NOTE.encode() in pdf.content
    assert signed["firmado_por"].encode("latin-1", "replace") in pdf.content


# ---------------------------------------------------------------------------
# Auto worker and manual route firing together (one pending report, R18 counter)
# ---------------------------------------------------------------------------


class GatedMl(FakeMl):
    """The first model call announces itself and waits, so the test decides
    when the path that holds the patient lock may finish."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def predict_criticality(self, payload: dict) -> dict:
        self.entered.set()
        assert self.release.wait(10), "the test never released the model"
        return super().predict_criticality(payload)


def _wait_until(condition, *, seconds: float = 10.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the other thread"
        time.sleep(0.01)


@pytest.mark.parametrize("first", ["auto", "manual"])
def test_auto_and_manual_firing_together_leave_one_pending_report(
    db, client_as, ml, monkeypatch, first
) -> None:
    patient = _create_patient(db, client_as)  # six core vitals + glucose; automation is off
    patient_id, encounter_id = uuid.UUID(patient["id"]), uuid.UUID(patient["encounter_id"])
    doctor = client_as("operador.norte")
    base = doctor.get("/api/v1/pendientes").json()["reportes_por_revisar"]

    gated = GatedMl()
    ml_client.set_client(gated)
    # Reactors write to the (single, shared) SQLite connection of the test; they
    # are not what is under test.
    monkeypatch.setattr(ai_reports, "announce_generated", lambda _db, _report: None)
    monkeypatch.setattr(ai_auto, "_tell_the_clinician", lambda _db, _patient, _report: None)

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    doctor_id = _user(db, "operador.norte").id
    results: dict[str, object] = {}
    errors: list[BaseException] = []
    done = {"auto": threading.Event(), "manual": threading.Event()}

    def automatic(session):  # noqa: ANN001, ANN202
        return ai_auto.analyze_encounter(session, encounter_id)

    def manual(session):  # noqa: ANN001, ANN202
        return ai_reports.request_analysis(
            session, session.get(Patient, patient_id), actor=session.get(User, doctor_id)
        )

    def run(name: str, work, other: str) -> None:  # noqa: ANN001
        try:
            with factory() as session:
                results[name] = work(session)
                done[name].set()
                # One shared connection: do not close (roll back) while the other
                # thread is still using it.
                assert done[other].wait(10)
        except BaseException as exc:  # noqa: BLE001 -- surfaced by the assertion below
            errors.append(exc)
            done[name].set()

    works = {"auto": (automatic, "manual"), "manual": (manual, "auto")}
    second = "manual" if first == "auto" else "auto"
    t_first = threading.Thread(target=run, args=(first, *works[first]))
    t_second = threading.Thread(target=run, args=(second, *works[second]))

    t_first.start()
    _wait_until(gated.entered.is_set)  # the first path is inside the model call, lock held
    t_second.start()
    _wait_until(lambda: patient_locks.users(patient_id) == 2)  # the second one waits for the lock
    assert not done[second].is_set()
    gated.release.set()
    t_first.join(15)
    t_second.join(15)
    assert not errors, errors

    (report,) = _reports(db)
    assert report.status == AiReportStatus.PENDING
    assert len(gated.criticality_calls) == 1, "the second path must not call the model again"
    if first == "auto":
        assert report.origin == AiReportOrigin.AUTOMATIC
        assert results["manual"].id == report.id  # the manual call returns the automatic report
    else:
        assert report.origin == AiReportOrigin.MANUAL
        assert results["auto"] is None  # the worker backs off: the manual report exists
        assert results["manual"].id == report.id
    assert patient_locks.users(patient_id) == 0, "lock entries are dropped when idle"

    # R18: one report to review, and approving it brings the counter back.
    assert doctor.get("/api/v1/pendientes").json()["reportes_por_revisar"] == base + 1
    assert doctor.post(f"/api/v1/ai-reports/{report.id}/approve").status_code == 200
    assert doctor.get("/api/v1/pendientes").json()["reportes_por_revisar"] == base


def test_a_different_payload_still_creates_its_own_manual_report(auto) -> None:
    db, client_as, patient, _ml = auto
    doctor = client_as("operador.norte")
    for vital in CORE:
        _post_vital(doctor, patient, vital)
    (automatic,) = _reports(db)
    other = _analyze(doctor, patient["id"], triage_level=2).json()
    assert other["id"] != str(automatic.id) and other["origen"] == "manual"
    assert len(_reports(db)) == 2


def test_the_worker_does_not_remake_a_report_already_signed_for_the_same_input(
    db, client_as, ml
) -> None:
    """A manual report approved before the worker ran must not come back as a new
    automatic pending report (it would put the R18 counter one above where it was)."""

    patient = _create_patient(db, client_as)  # six core vitals; automation is off
    doctor = client_as("operador.norte")
    manual = _analyze(doctor, patient["id"]).json()
    assert doctor.post(f"/api/v1/ai-reports/{manual['id']}/approve").status_code == 200

    assert ai_auto.analyze_encounter(db, uuid.UUID(patient["encounter_id"])) is None
    assert [str(r.id) for r in _reports(db)] == [manual["id"]]
    assert len(ml.criticality_calls) == 1


def test_an_unsigned_automatic_report_is_not_in_the_record(auto) -> None:
    db, client_as, patient, _ml = auto
    for vital in CORE:
        _post_vital(client_as("operador.norte"), patient, vital)
    record = client_as("operador.norte").get(f"/api/v1/patients/{patient['id']}/clinical-record").json()
    assert record["reportes_ia"] == []
