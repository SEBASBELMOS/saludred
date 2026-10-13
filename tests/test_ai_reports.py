# ruff: noqa: F811
"""R10 (AI report with clinical sign-off), R11 (clinical record PDF) and R12
(minimization and pseudonymization towards the AI service).

The AI service is replaced by a fake: no test opens a socket. The scene is a
patient created through the real API with "canary" personal data, so the R12
check is the one the course tester performs.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AiReport, AuditLog, BedRequest, BedStatusEvent, Patient
from app.models.enums import AiReportStatus, AuditAction, BedStatus
from app.services import beds as beds_service
from app.services import ml_client, pseudonymization
from app.services.errors import MlServiceUnavailableError
from tests.test_bed_rules import _user, client_as, db  # noqa: F401  (fixture reuse)

CANARY = {
    "document_type": "CC",
    "document_number": "7788990011",
    "first_name": "Zacarias",
    "last_name": "Quimbayaxx",
    "birth_date": "1961-03-09",
    "gender": "male",
    "phone": "3109998877",
    "email": "canary.zacarias@canario.test",
    "address": "Carrera 77 numero 88-99 Canarilandia",
}
VITALS = [
    ("8310-5", "Body temperature", 38.5, "Cel"),
    ("8867-4", "Heart rate", 118, "/min"),
    ("9279-1", "Respiratory rate", 26, "/min"),
    ("59408-5", "Oxygen saturation", 88, "%"),
    ("8480-6", "Systolic blood pressure", 100, "mm[Hg]"),
    ("8462-4", "Diastolic blood pressure", 60, "mm[Hg]"),
    ("2339-0", "Glucose", 182, "mg/dL"),  # not a model variable: must not be sent
]


class FakeMl:
    """Answers like the real service and remembers what it was sent."""

    def __init__(self, probability: float = 0.91) -> None:
        self.probability = probability
        self.criticality_calls: list[dict] = []
        self.cleaning_calls: list[dict] = []

    def predict_criticality(self, payload: dict) -> dict:
        self.criticality_calls.append(payload)
        return {
            "probability": self.probability,
            "nivel": "critico" if self.probability >= 0.465 else "no_critico",
            "positive": self.probability >= 0.465,
            "threshold": 0.465,
            "model_version": "criticality-rf-1.0-test",
            # Fields the service added with the model comparison: the API ignores them.
            "model_family": "rf",
            "explanation": "cambio de probabilidad al reemplazar la variable por su valor tipico",
            "imputed": ["acuity"],
            "unrecognized": [],
            "top_features": [
                {"feature": "o2sat", "value": 88.0, "contribution": 0.14, "imputed": False}
            ],
            "pseudonym": payload["pseudonym"],
        }

    def predict_cleaning_delay(self, payload: dict) -> dict:
        self.cleaning_calls.append(payload)
        return {
            "probability": 0.7312,
            "nivel": "retraso",
            "positive": True,
            "threshold": 0.15,
            "model_version": "cleaning-delay-gb-1.0-test",
            "model_family": "gb",
            "explanation": "cambio de probabilidad al reemplazar la variable por su valor tipico",
            "imputed": [],
            "unrecognized": [],
            "top_features": [],
            "pseudonym": None,
        }

    def models(self) -> dict:
        return {"criticality": {"model_version": "criticality-rf-1.0-test"}}


@pytest.fixture
def ml() -> FakeMl:
    fake = FakeMl()
    ml_client.set_client(fake)
    return fake


def _create_patient(db, client_as, *, with_vitals: bool = True) -> dict:
    client = client_as("operador.norte")
    org = _user(db, "operador.norte").organization_id
    eps = db.scalars(select(Patient)).first().eps_organization_id
    created = client.post(
        "/api/v1/patients",
        json={**CANARY, "eps_organization_id": str(eps)},
    )
    assert created.status_code == 201, created.text
    patient = created.json()
    encounter = client.post(
        "/api/v1/encounters",
        json={
            "patient_id": patient["id"],
            "organization_id": str(org),
            "encounter_class": "EMER",
            "status": "in-progress",
            "priority": "URGENT",
            "reason_text": "Dificultad respiratoria",
            "started_at": datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert encounter.status_code == 201, encounter.text
    patient["encounter_id"] = encounter.json()["id"]
    if with_vitals:
        for code, display, value, unit in VITALS:
            response = client.post(
                "/api/v1/observations",
                json={
                    "patient_id": patient["id"],
                    "encounter_id": patient["encounter_id"],
                    "code": code,
                    "display": display,
                    "value_numeric": value,
                    "unit": unit,
                    "observed_at": datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc).isoformat(),
                },
            )
            assert response.status_code == 201, response.text
    return patient


def _analyze(client, patient_id: str, **body):
    return client.post(f"/api/v1/patients/{patient_id}/ai-analyses", json=body or None)


@pytest.fixture
def scene(db, client_as, ml):
    patient = _create_patient(db, client_as)
    return db, client_as, patient, ml


# ---------------------------------------------------------------------------
# R12 -- minimization and pseudonymization
# ---------------------------------------------------------------------------


def test_no_canary_value_reaches_the_stored_payload(scene) -> None:
    db, client_as, patient, ml = scene
    response = _analyze(client_as("operador.norte"), patient["id"])
    assert response.status_code == 201, response.text
    report_id = response.json()["id"]

    exposed = client_as("operador.norte").get(f"/api/v1/ai-reports/{report_id}/payload")
    assert exposed.status_code == 200
    payload = exposed.json()["payload"]

    row = db.get(AiReport, uuid.UUID(report_id))
    everything = json.dumps(
        [payload, row.payload_sent, row.model_response, ml.criticality_calls], default=str
    )
    forbidden = [
        *[v for k, v in CANARY.items() if k not in {"document_type", "gender"}],
        patient["id"],
        "1961",  # birth year: only the integer age is allowed
        "Zacarias",
        "Quimbayaxx",
        "canario",
    ]
    for value in forbidden:
        assert value not in everything, value

    # What was exposed is exactly what was sent, and nothing but the allowlist.
    assert payload == ml.criticality_calls[0] == row.payload_sent
    assert set(payload) <= set(pseudonymization.PAYLOAD_FIELDS)
    assert "glucose" not in everything and 182 not in payload.values()


def test_payload_content_and_pseudonym(scene) -> None:
    _db, client_as, patient, ml = scene
    _analyze(client_as("operador.norte"), patient["id"], triage_level=2)
    payload = ml.criticality_calls[0]
    expected_age = pseudonymization.age_in_years(date(1961, 3, 9))
    assert payload == {
        "pseudonym": pseudonymization.pseudonym_for(patient["id"]),
        "age": expected_age,
        "sex": "male",
        "temperature_c": 38.5,
        "heart_rate": 118.0,
        "resp_rate": 26.0,
        "o2sat": 88.0,
        "sbp": 100.0,
        "dbp": 60.0,
        "acuity": 2,
    }


def test_pseudonym_is_stable_opaque_and_keyed(monkeypatch) -> None:
    patient_id = uuid.uuid4()
    first = pseudonymization.pseudonym_for(patient_id)
    assert first == pseudonymization.pseudonym_for(str(patient_id))
    assert first.startswith("pz-") and len(first) == 19
    assert all(c in "0123456789abcdef" for c in first[3:])
    assert first != pseudonymization.pseudonym_for(uuid.uuid4())
    assert patient_id.hex[:8] not in first
    monkeypatch.setattr(get_settings(), "pseudonym_key", "another-key-for-the-test")
    assert pseudonymization.pseudonym_for(patient_id) != first


def test_a_patient_without_data_is_not_analyzed(db, client_as, ml) -> None:
    patient = _create_patient(db, client_as, with_vitals=False)
    response = _analyze(client_as("operador.norte"), patient["id"])
    assert response.status_code == 422
    assert ml.criticality_calls == []
    assert db.scalars(select(AiReport)).first() is None


# ---------------------------------------------------------------------------
# R10 -- soft put
# ---------------------------------------------------------------------------


def test_full_flow_one_approved_one_rejected(scene) -> None:
    db, client_as, patient, _ml = scene
    doctor = client_as("operador.norte")

    first = _analyze(doctor, patient["id"])
    assert first.status_code == 201
    assert first.json()["estado"] == "pendiente"
    approved_id = first.json()["id"]
    rejected_id = _analyze(doctor, patient["id"]).json()["id"]
    pending_id = _analyze(doctor, patient["id"]).json()["id"]

    # Pending reports are not part of the record.
    record = doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record").json()
    assert record["reportes_ia"] == []

    ok = doctor.post(
        f"/api/v1/ai-reports/{approved_id}/approve",
        json={"observaciones": "Concuerda con la clinica"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["estado"] == "aprobado"
    assert body["observaciones"] == "Concuerda con la clinica"
    assert body["reviewed_by"] == str(_user(db, "operador.norte").id)
    assert body["firmado_por"] == _user(db, "operador.norte").full_name

    no = doctor.post(
        f"/api/v1/ai-reports/{rejected_id}/reject",
        json={"motivo": "No coincide con el examen fisico", "observaciones": "Repetir signos"},
    )
    assert no.status_code == 200, no.text
    assert no.json()["estado"] == "rechazado"
    assert no.json()["motivo_rechazo"] == "No coincide con el examen fisico"

    # A rejected report is not deleted: it can still be read.
    kept = doctor.get(f"/api/v1/ai-reports/{rejected_id}")
    assert kept.status_code == 200 and kept.json()["estado"] == "rechazado"
    assert doctor.get(f"/api/v1/ai-reports/{rejected_id}/payload").status_code == 200
    assert doctor.delete(f"/api/v1/ai-reports/{rejected_id}").status_code == 405

    # Work tray filter.
    listed = doctor.get("/api/v1/ai-reports?estado=pendiente").json()
    assert [r["id"] for r in listed] == [pending_id]
    assert {r["estado"] for r in doctor.get(
        f"/api/v1/ai-reports?patient_id={patient['id']}").json()} == {
        "pendiente", "aprobado", "rechazado"}

    # Clinical record: only the approved one, with its id.
    record = doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record").json()
    assert [r["id"] for r in record["reportes_ia"]] == [approved_id]
    assert record["reportes_ia"][0]["estado"] == "aprobado"
    assert record["paciente"]["first_name"] == "Zacarias"
    assert len(record["observaciones"]) == len(VITALS)

    # PDF (R11).
    pdf = doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert b"Zacarias Quimbayaxx" in pdf.content
    assert approved_id.encode() in pdf.content
    assert rejected_id.encode() not in pdf.content
    assert pending_id.encode() not in pdf.content
    assert b"Concuerda con la clinica" in pdf.content


def test_a_report_is_reviewed_once(scene) -> None:
    _db, client_as, patient, _ml = scene
    doctor = client_as("operador.norte")
    a = _analyze(doctor, patient["id"]).json()["id"]
    b = _analyze(doctor, patient["id"]).json()["id"]
    assert doctor.post(f"/api/v1/ai-reports/{a}/approve", json={}).status_code == 200
    assert doctor.post(f"/api/v1/ai-reports/{a}/approve", json={}).status_code == 409
    assert doctor.post(f"/api/v1/ai-reports/{a}/reject", json={"motivo": "tarde"}).status_code == 409
    assert doctor.post(f"/api/v1/ai-reports/{b}/reject", json={"motivo": "no aplica"}).status_code == 200
    assert doctor.post(f"/api/v1/ai-reports/{b}/approve", json={}).status_code == 409
    assert doctor.get(f"/api/v1/ai-reports/{b}").json()["estado"] == "rechazado"


def test_a_deleted_patient_leaves_the_work_tray(scene) -> None:
    _db, client_as, patient, _ml = scene
    doctor = client_as("operador.norte")
    report_id = _analyze(doctor, patient["id"]).json()["id"]
    assert [r["id"] for r in doctor.get("/api/v1/ai-reports?estado=pendiente").json()] == [report_id]

    assert client_as("admin").delete(f"/api/v1/patients/{patient['id']}").status_code == 204
    assert doctor.get("/api/v1/ai-reports?estado=pendiente").json() == []
    # The report itself is kept as evidence.
    assert client_as("admin").get(f"/api/v1/ai-reports/{report_id}").status_code == 200


def test_rejecting_requires_a_reason(scene) -> None:
    _db, client_as, patient, _ml = scene
    doctor = client_as("operador.norte")
    report = _analyze(doctor, patient["id"]).json()["id"]
    assert doctor.post(f"/api/v1/ai-reports/{report}/reject", json={}).status_code == 422
    assert doctor.post(f"/api/v1/ai-reports/{report}/reject", json={"motivo": "  "}).status_code == 422
    assert doctor.get(f"/api/v1/ai-reports/{report}").json()["estado"] == "pendiente"


def test_a_specialist_can_also_sign(scene) -> None:
    db, client_as, patient, _ml = scene
    report = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    response = client_as("especialista.norte").post(f"/api/v1/ai-reports/{report}/approve", json={})
    assert response.status_code == 200, response.text
    assert response.json()["firmado_por"] == _user(db, "especialista.norte").full_name


@pytest.mark.parametrize("username", ["paciente.demo", "contable.eps"])
def test_patient_and_accountant_get_403_everywhere(scene, username) -> None:
    _db, client_as, patient, _ml = scene
    report = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    client = client_as(username)
    assert _analyze(client, patient["id"]).status_code == 403
    assert client.post(f"/api/v1/ai-reports/{report}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/ai-reports/{report}/reject", json={"motivo": "xx yy"}).status_code == 403
    assert client.get(f"/api/v1/ai-reports/{report}").status_code == 403
    assert client.get(f"/api/v1/ai-reports/{report}/payload").status_code == 403
    assert client.get("/api/v1/ai-reports").status_code == 403
    assert client.get("/api/v1/ai/models").status_code == 403


@pytest.mark.parametrize("username", ["admin", "coordinador.eps"])
def test_admin_and_coordinator_read_but_never_sign(scene, username) -> None:
    _db, client_as, patient, _ml = scene
    report = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    client = client_as(username)
    assert _analyze(client, patient["id"]).status_code == 403
    assert client.post(f"/api/v1/ai-reports/{report}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/ai-reports/{report}/reject", json={"motivo": "xx yy"}).status_code == 403
    assert client.get(f"/api/v1/ai-reports/{report}").status_code == 200
    assert client.get(f"/api/v1/ai-reports/{report}/payload").status_code == 200


def test_another_ips_cannot_reach_the_patient_or_its_reports(scene) -> None:
    _db, client_as, patient, _ml = scene
    report = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    outsider = client_as("operador.sur")
    assert _analyze(outsider, patient["id"]).status_code == 403
    assert outsider.get(f"/api/v1/ai-reports/{report}").status_code == 403
    assert outsider.post(f"/api/v1/ai-reports/{report}/approve", json={}).status_code == 403
    assert outsider.get(f"/api/v1/patients/{patient['id']}/clinical-record").status_code == 403
    assert [r["id"] for r in outsider.get("/api/v1/ai-reports").json()] == []


def test_report_evidence_cannot_be_rewritten(scene) -> None:
    db, client_as, patient, _ml = scene
    report_id = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    row = db.get(AiReport, uuid.UUID(report_id))
    row.payload_sent = {"pseudonym": "pz-0000000000000000"}
    with pytest.raises(ValueError, match="inmutable"):
        db.flush()
    db.rollback()
    row = db.get(AiReport, uuid.UUID(report_id))
    row.status = AiReportStatus.APPROVED  # the sign-off itself is allowed to change
    db.flush()


def test_every_step_is_audited(scene) -> None:
    db, client_as, patient, _ml = scene
    doctor = client_as("operador.norte")
    approved = _analyze(doctor, patient["id"]).json()["id"]
    rejected = _analyze(doctor, patient["id"]).json()["id"]
    doctor.post(f"/api/v1/ai-reports/{approved}/approve", json={})
    doctor.post(f"/api/v1/ai-reports/{rejected}/reject", json={"motivo": "no concuerda"})
    doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record")
    doctor.get(f"/api/v1/patients/{patient['id']}/clinical-record.pdf")

    def entries(action: AuditAction) -> list[AuditLog]:
        return list(db.scalars(select(AuditLog).where(AuditLog.action == action)))

    assert {e.metadata_json["report_id"] for e in entries(AuditAction.AI_ANALYSIS)} == {approved, rejected}
    assert all(e.metadata_json["patient_id"] == patient["id"] for e in entries(AuditAction.AI_ANALYSIS))
    assert [e.metadata_json["report_id"] for e in entries(AuditAction.REPORT_APPROVED)] == [approved]
    rejection = entries(AuditAction.REPORT_REJECTED)
    assert [e.metadata_json["report_id"] for e in rejection] == [rejected]
    assert rejection[0].metadata_json["motivo"] == "no concuerda"
    assert entries(AuditAction.RECORD_VIEWED)[0].metadata_json["format"] == "json"
    export = entries(AuditAction.CLINICAL_RECORD_EXPORTED)
    assert export[0].metadata_json["approved_report_ids"] == [approved]
    assert export[0].entity_id == uuid.UUID(patient["id"])


# ---------------------------------------------------------------------------
# The AI never decides: only an approved report reaches the queue
# ---------------------------------------------------------------------------


def _open_request(db, patient: dict) -> BedRequest:
    from app.schemas.beds import BedRequestCreate

    return beds_service.create_bed_request(
        db,
        BedRequestCreate(
            encounter_id=uuid.UUID(patient["encounter_id"]),
            required_service="UCI Adultos",
            priority="URGENT",
        ),
        actor=_user(db, "operador.norte"),
    )


def test_only_an_approved_report_sets_the_bed_request_criticality(scene) -> None:
    db, client_as, patient, _ml = scene
    request = _open_request(db, patient)
    assert request.criticality_score is None
    doctor = client_as("operador.norte")

    pending = _analyze(doctor, patient["id"]).json()["id"]
    db.refresh(request)
    assert request.criticality_score is None  # pending never touches the queue

    doctor.post(f"/api/v1/ai-reports/{pending}/reject", json={"motivo": "no concuerda"})
    db.refresh(request)
    assert request.criticality_score is None  # neither does a rejection

    approved = _analyze(doctor, patient["id"]).json()["id"]
    body = doctor.post(f"/api/v1/ai-reports/{approved}/approve", json={}).json()
    db.refresh(request)
    assert float(request.criticality_score) == pytest.approx(0.91)
    assert body["solicitud_cama_id"] == str(request.id)
    entry = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.REPORT_APPROVED)
    ).one()
    assert entry.metadata_json["bed_request_id"] == str(request.id)
    assert entry.metadata_json["criticality_score"] == pytest.approx(0.91)


def test_approving_without_an_open_request_changes_no_queue(scene) -> None:
    db, client_as, patient, _ml = scene
    report = _analyze(client_as("operador.norte"), patient["id"]).json()["id"]
    response = client_as("operador.norte").post(f"/api/v1/ai-reports/{report}/approve", json={})
    assert response.status_code == 200
    assert response.json()["solicitud_cama_id"] is None
    assert db.scalars(select(BedRequest).where(BedRequest.criticality_score.is_not(None))).first() is None


# ---------------------------------------------------------------------------
# AI service down
# ---------------------------------------------------------------------------


def test_ml_down_answers_503_and_stores_nothing(db, client_as) -> None:
    patient = _create_patient(db, client_as)  # default client: service down
    response = _analyze(client_as("operador.norte"), patient["id"])
    assert response.status_code == 503
    assert "servicio de IA" in response.json()["detail"]
    assert db.scalars(select(AiReport)).first() is None
    assert db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.AI_ANALYSIS)).first() is None


def test_ml_rejection_is_a_502_and_stores_nothing(db, client_as) -> None:
    from app.services.errors import MlServiceRejectedError

    class Rejecting(FakeMl):
        def predict_criticality(self, payload: dict) -> dict:
            raise MlServiceRejectedError("rechazado")

    ml_client.set_client(Rejecting())
    patient = _create_patient(db, client_as)
    assert _analyze(client_as("operador.norte"), patient["id"]).status_code == 502
    assert db.scalars(select(AiReport)).first() is None


def test_models_endpoint_proxies_and_degrades(db, client_as, ml) -> None:
    assert client_as("operador.norte").get("/api/v1/ai/models").json() == ml.models()

    class Down:
        def models(self) -> dict:
            raise MlServiceUnavailableError("down")

    ml_client.set_client(Down())
    assert client_as("admin").get("/api/v1/ai/models").status_code == 503


# ---------------------------------------------------------------------------
# Clinical record access
# ---------------------------------------------------------------------------


def test_a_patient_downloads_only_their_own_record(db, client_as, ml) -> None:
    own = _user(db, "paciente.demo").patient_id
    other = db.scalars(select(Patient).where(Patient.id != own)).first()
    client = client_as("paciente.demo")
    mine = client.get(f"/api/v1/patients/{own}/clinical-record.pdf")
    assert mine.status_code == 200 and mine.content.startswith(b"%PDF")
    assert client.get(f"/api/v1/patients/{own}/clinical-record").status_code == 200
    assert client.get(f"/api/v1/patients/{other.id}/clinical-record.pdf").status_code == 403
    assert client.get(f"/api/v1/patients/{other.id}/clinical-record").status_code == 403
    export = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.CLINICAL_RECORD_EXPORTED)
    ).one()
    assert export.metadata_json["own_record"] is True


def test_the_accountant_gets_no_clinical_record(db, client_as) -> None:
    patient = db.scalars(select(Patient)).first()
    client = client_as("contable.eps")
    assert client.get(f"/api/v1/patients/{patient.id}/clinical-record").status_code == 403
    assert client.get(f"/api/v1/patients/{patient.id}/clinical-record.pdf").status_code == 403


def test_the_record_of_an_unknown_patient_is_404(db, client_as) -> None:
    assert client_as("admin").get(f"/api/v1/patients/{uuid.uuid4()}/clinical-record").status_code == 404


# ---------------------------------------------------------------------------
# Cleaning-delay prediction (operational, best effort)
# ---------------------------------------------------------------------------

OPERATIONAL_KEYS = {
    "service", "release_reason", "release_hour", "weekday", "previous_stay_hours",
    "pending_cleaning_beds", "service_occupancy", "patient_waiting",
}


def _occupied_bed(db):
    from app.models import BedAssignment, Location
    from app.models.enums import BedAssignmentStatus

    return db.scalar(
        select(Location)
        .join(BedAssignment, BedAssignment.location_id == Location.id)
        .where(BedAssignment.status == BedAssignmentStatus.ACTIVE)
        .limit(1)
    )


def _entry_event(db, bed) -> BedStatusEvent:
    """The event written by the release under test (the seed has older ones)."""

    return db.scalars(
        select(BedStatusEvent)
        .where(
            BedStatusEvent.location_id == bed.id,
            BedStatusEvent.new_status == BedStatus.CLEANING,
            BedStatusEvent.changed_by == _user(db, "admin").id,
        )
        .order_by(BedStatusEvent.event_at.desc())
    ).first()


def test_releasing_a_bed_stores_the_prediction_and_sends_no_patient_data(db, client_as, ml) -> None:
    bed = _occupied_bed(db)
    assignment = beds_service.active_assignment_for_bed(db, bed.id)
    beds_service.release_bed(db, assignment, actor=_user(db, "admin"))

    event = _entry_event(db, bed)
    assert float(event.predicted_delay_probability) == pytest.approx(0.7312)
    assert event.predicted_delay is True
    assert event.delay_model_version == "cleaning-delay-gb-1.0-test"

    sent = ml.cleaning_calls[0]
    assert set(sent) <= OPERATIONAL_KEYS
    assert sent["service"] == bed.service and sent["release_reason"] == "discharge"
    assert 0 <= sent["release_hour"] <= 23 and 1 <= sent["weekday"] <= 7


def test_the_prediction_reaches_the_cleaning_record_and_the_overdue_list(db, client_as, ml) -> None:
    from tests.test_bed_cleaning import _create, _cleaning_bed

    bed = _cleaning_bed(db)  # AVAILABLE -> CLEANING through the real service
    overdue = client_as("coordinador.eps").get("/api/v1/beds/cleaning/overdue?minutes=0").json()
    item = next(i for i in overdue if i["location_id"] == str(bed.id))
    assert item["predicted_delay"] is True
    assert item["predicted_delay_probability"] == pytest.approx(0.7312)

    record = _create(client_as("operador.norte"), bed)
    assert record.status_code == 201, record.text
    assert record.json()["predicted_delay_probability"] == pytest.approx(0.7312)
    assert record.json()["predicted_delay"] is True
    assert record.json()["delay_model_version"] == "cleaning-delay-gb-1.0-test"


def test_the_bed_moves_even_if_the_ml_service_is_down(db, client_as) -> None:
    # Default client for the whole suite: the AI service is unreachable.
    bed = _occupied_bed(db)
    assignment = beds_service.active_assignment_for_bed(db, bed.id)
    beds_service.release_bed(db, assignment, actor=_user(db, "admin"))
    db.refresh(bed)
    assert bed.status == BedStatus.CLEANING
    event = _entry_event(db, bed)
    assert event.predicted_delay_probability is None and event.predicted_delay is None


def test_openapi_documents_the_ai_endpoints(client_as, db) -> None:
    paths = client_as("admin").get("/openapi.json").json()["paths"]
    assert {
        "/api/v1/patients/{patient_id}/ai-analyses",
        "/api/v1/ai-reports",
        "/api/v1/ai-reports/{report_id}",
        "/api/v1/ai-reports/{report_id}/payload",
        "/api/v1/ai-reports/{report_id}/approve",
        "/api/v1/ai-reports/{report_id}/reject",
        "/api/v1/ai/models",
        "/api/v1/patients/{patient_id}/clinical-record",
        "/api/v1/patients/{patient_id}/clinical-record.pdf",
    } <= set(paths)
    assert "delete" not in paths["/api/v1/ai-reports/{report_id}"]
