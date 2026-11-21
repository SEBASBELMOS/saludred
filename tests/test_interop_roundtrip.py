# ruff: noqa: F811
"""R30: the referral round trip A -> B -> A inside ONE application, over the real HTTP routes.

"Our own system" is registered as a partner. The sender's transport is the FastAPI
``TestClient`` (no network): A posts the Bundle to ``/interop/Bundle``, B answers with a
``Task`` to ``/interop/Task``. Nothing is mocked in between: token, signature, validation,
consent, audit and live events all run.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.fhir.diagnostic_report import approved_diagnostic_report
from app.main import app
from app.models import (
    FhirInboundEvent,
    InteropTaskOutbox,
    Observation,
    OutboundReferral,
    PartnerDiagnosticReport,
    Referral,
)
from app.models.enums import (
    AiReportStatus,
    AuditAction,
    ObservationOrigin,
    ObservationStatus,
    ReferralStatus,
)
from app.models.patient import Patient
from app.services import referrals as referrals_service
from app.services.interop import clientes, destinations, envio, receptor, roundtrip
from app.services.interop.cliente import InteropSender, SenderError, SendResult, encode
from tests.interop_helpers import (
    FEDERATED_SECRET,
    federated,
    get_token,
    observation,
    outcome_issues,
    register,
    signed_post,
)
from tests.reactive_helpers import Actor, audit_rows, inbox
from tests.test_ai_reports import CANARY, _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

SELF = "saludred-self"
ALL_SCOPES = (
    clientes.SCOPE_OBSERVATION,
    clientes.SCOPE_SERVICE_REQUEST,
    clientes.SCOPE_BUNDLE,
    clientes.SCOPE_TASK,
)
DOC = (CANARY["document_type"], CANARY["document_number"])
REFERRALS = "/api/v1/referrals"
OUTBOUND = "/api/v1/interop/remisiones"


class Wire:
    """The transport between A and B: records every message and can simulate an outage."""

    def __init__(self, http: TestClient, creds) -> None:  # noqa: ANN001
        self.http, self.creds = http, creds
        self.sent: list[tuple[str, dict]] = []
        self.down = False
        self.built = 0

    def sender(self, _db, _client_id: str) -> InteropSender:  # noqa: ANN001
        self.built += 1
        wire = self

        def refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("partner unreachable")

        class Spy(InteropSender):
            def _enviar(self, resource_type: str, resource: dict):  # noqa: ANN202
                wire.sent.append((resource_type, deepcopy(resource)))
                return super()._enviar(resource_type, resource)

        return Spy(
            "http://testserver", wire.creds.client_id, wire.creds.client_secret,
            wire.creds.signing_secret,
            http=httpx.Client(transport=httpx.MockTransport(refuse)) if self.down else self.http,
            scopes=(clientes.SCOPE_BUNDLE, clientes.SCOPE_TASK), retries=0, sleep=lambda _s: None,
        )

    def of(self, kind: str) -> list[dict]:
        return [resource for name, resource in self.sent if name == kind]


class Loop:
    def __init__(self, db, client_as, patient, wire, http, creds) -> None:  # noqa: ANN001
        self.db, self.patient, self.wire, self.http, self.creds = db, patient, wire, http, creds
        self.doctor = Actor(client_as, "operador.norte")
        self.specialist = Actor(client_as, "especialista.norte")
        self.admin = Actor(client_as, "admin")
        self.approved = ""

    def grant_consent(self) -> None:
        response = self.doctor.post("/api/v1/consents", json={
            "paciente_id": self.patient["id"], "cliente_interop_id": SELF,
            "vigente_desde": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        })
        assert response.status_code == 201, response.text

    def refer(self, **overrides):  # noqa: ANN003, ANN201
        body = {
            "paciente_id": self.patient["id"], "cliente_interop_id": SELF,
            "motivo": "Control de diabetes tipo 2", "servicio": "Endocrinologia",
            "prioridad": "urgente", **overrides,
        }
        return self.doctor.post(OUTBOUND, json=body)

    def sent_referral(self) -> dict:
        response = self.refer()
        assert response.status_code == 201, response.text
        return response.json()

    def received(self) -> Referral:
        self.db.expire_all()
        return self.db.scalar(select(Referral).where(Referral.origen_externo == SELF))

    def outbound(self) -> list[dict]:
        return self.doctor.get(OUTBOUND).json()


@pytest.fixture
def loop(db, client_as, ml, monkeypatch):  # noqa: ANN001, ANN201
    settings = get_settings()
    monkeypatch.setattr(settings, "federated_secret", FEDERATED_SECRET)
    monkeypatch.setattr(settings, "interop_default_specialist", "especialista.norte")
    # With vitals: the AI analysis needs them, and glucose / blood pressure are profile LOINC codes.
    patient = _create_patient(db, client_as)
    doctor = client_as("operador.norte")
    approved = _analyze(doctor, patient["id"]).json()["id"]
    _analyze(doctor, patient["id"], triage_level=2)  # stays pending
    rejected = _analyze(doctor, patient["id"], triage_level=3).json()["id"]
    doctor.post(f"/api/v1/ai-reports/{approved}/approve", json={"observaciones": "Concuerda"})
    doctor.post(f"/api/v1/ai-reports/{rejected}/reject", json={"motivo": "No aplica"})

    creds = register(db, SELF, ALL_SCOPES)
    http = TestClient(app)
    wire = Wire(http, creds)
    monkeypatch.setattr(destinations, "sender_for_partner", wire.sender)
    # The answer to the partner is sent by a background task with its OWN session.
    monkeypatch.setattr(roundtrip, "_session_factory", sessionmaker(bind=db.get_bind(), expire_on_commit=False))
    scene = Loop(db, client_as, patient, wire, http, creds)
    scene.approved = approved
    return scene


def _interop_rows(db, action: AuditAction, resource: str) -> list:  # noqa: ANN001
    db.expire_all()
    return [r for r in audit_rows(db, action) if (r.metadata_json or {}).get("recurso") == resource]


def _count(db, model, *where) -> int:  # noqa: ANN001, ANN002
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


# -- 1. AI result as an approved DiagnosticReport -------------------------------------------------


def _ai_report(status: AiReportStatus) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(id=uuid.uuid4(), status=status, level="critico", model_version="m-1",
                           reviewed_at=now, created_at=now)


def test_an_approved_ai_report_becomes_a_final_diagnostic_report() -> None:
    report = _ai_report(AiReportStatus.APPROVED)
    resource = approved_diagnostic_report(report)
    assert resource["resourceType"] == "DiagnosticReport" and resource["status"] == "final"
    assert resource["code"]["coding"][0]["system"] == "http://loinc.org"
    assert resource["conclusionCode"][0]["coding"][0]["code"] == "critico"
    assert "critico" in resource["conclusion"] and resource["issued"]
    assert resource["extension"][0]["valueString"] == "m-1"
    assert resource["subject"] == {"reference": ""}  # the Bundle builder puts the federated subject


@pytest.mark.parametrize("status", [AiReportStatus.PENDING, AiReportStatus.REJECTED])
def test_a_pending_or_rejected_report_is_never_exported(status) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        approved_diagnostic_report(_ai_report(status))


# -- 2. A: the doctor refers; consent first -------------------------------------------------------


def test_the_consent_gate_blocks_before_anything_is_built_or_sent(loop) -> None:
    response = loop.refer()
    assert response.status_code == 403
    assert loop.wire.sent == [] and loop.wire.built == 0  # the transport was never even configured
    assert _count(loop.db, OutboundReferral) == 0 and loop.received() is None
    (denied,) = audit_rows(loop.db, AuditAction.INTEROP_REJECTED)
    assert denied.metadata_json["reason"] == "consent_denied"


def test_only_a_medico_may_send_a_referral_to_a_partner(loop) -> None:
    loop.grant_consent()
    for username in ("especialista.norte", "admin", "paciente.demo"):
        denied = Actor(loop.doctor._client_as, username).post(OUTBOUND, json={
            "paciente_id": loop.patient["id"], "cliente_interop_id": SELF, "motivo": "x",
            "servicio": "y"})
        assert denied.status_code == 403, username
    assert loop.wire.sent == []


def test_the_bundle_is_minimal_and_carries_only_approved_evidence(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    assert sent["estado"] == "enviada" and sent["cliente_interop_id"] == SELF
    (bundle,) = loop.wire.of("Bundle")
    resources = [entry["resource"] for entry in bundle["entry"]]
    kinds = [r["resourceType"] for r in resources]
    assert kinds.count("Patient") == 1 and kinds.count("ServiceRequest") == 1
    assert "Encounter" not in kinds and "Observation" in kinds

    patient = next(r for r in resources if r["resourceType"] == "Patient")
    assert set(patient) == {"resourceType", "identifier"}
    assert patient["identifier"][0]["value"] == federated(*DOC)
    text = json.dumps(bundle)
    for secret in (CANARY["document_number"], CANARY["phone"], CANARY["email"],
                   CANARY["address"], CANARY["first_name"], CANARY["last_name"]):
        assert secret not in text
    for key in ("telecom", "address", "name", "birthDate"):
        assert f'"{key}"' not in text

    request = next(r for r in resources if r["resourceType"] == "ServiceRequest")
    assert request["identifier"][0]["value"] == sent["solicitud_id"]
    assert request["code"]["coding"][0] == {
        "system": "http://snomed.info/sct", "code": "3457005", "display": "Patient referral"}
    assert request["priority"] == "urgent" and request["orderDetail"] == [{"text": "Endocrinologia"}]

    reports = [r for r in resources if r["resourceType"] == "DiagnosticReport"]
    assert [r["id"] for r in reports] == [loop.approved]  # the pending and the rejected one stay home
    assert all(r["status"] == "final" for r in reports)
    assert all(r["subject"]["identifier"]["value"] == federated(*DOC) for r in resources[1:])
    (row,) = _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Bundle")
    assert row.metadata_json["entregado"] is True and row.metadata_json["codigo_http"] == 201


# -- 3. B: the Bundle entry endpoint ----------------------------------------------------------------


def test_b_creates_the_referral_stores_the_evidence_and_tells_the_specialist(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    referral = loop.received()
    assert referral.status == ReferralStatus.PENDING and referral.referring_user_id is None
    assert referral.referencia_externa == sent["solicitud_id"]
    assert "Control de diabetes tipo 2" in referral.motivo and "Endocrinologia" in referral.motivo
    assert referral.specialist_user_id == _user(loop.db, "especialista.norte").id

    # The evidence of the partner is kept apart from the locally approved analyses.
    assert _count(loop.db, PartnerDiagnosticReport) == 1
    (bundle,) = loop.wire.of("Bundle")
    travelled = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"]
    assert travelled  # the profile observations of the patient (glucose, blood pressure)
    assert _count(loop.db, Observation, Observation.origin == ObservationOrigin.FHIR) == len(travelled)

    (note,) = inbox(loop.db, "especialista.norte", "remision_creada")
    assert note.data["origen_externo"] == SELF and note.data["remision_id"] == str(referral.id)
    listed = [r for r in loop.specialist.get(REFERRALS).json() if r["id"] == str(referral.id)]
    assert [r["remitente_nombre"] for r in listed] == [f"Sistema externo: {SELF}"]

    (received,) = _interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "Bundle")
    assert received.metadata_json["origen"] == "externo" and received.metadata_json["cliente"] == SELF
    assert received.metadata_json["remision_id"] == str(referral.id)
    created = [r for r in audit_rows(loop.db, AuditAction.REFERRAL_CREATED)
               if r.metadata_json.get("origen") == "externo"]
    assert len(created) == 1


def _hand_bundle(*extra: dict, patient: dict | None = None, request: dict | None = None) -> dict:
    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    order = request or roundtrip.build_referral_request(
        f"sr-{uuid.uuid4().hex}", "Control", "Cardiologia", "rutina")
    order["subject"] = {"identifier": fid}
    resources = [patient or {"resourceType": "Patient", "identifier": [fid]}, order,
                 observation(*DOC, value=150, resource_id=f"obs-{uuid.uuid4().hex[:8]}"), *extra]
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in resources]}


def _post(loop, path: str, resource: dict, **kw):  # noqa: ANN001, ANN003, ANN201
    return signed_post(loop.http, path, resource, loop.creds, get_token(loop.http, loop.creds), **kw)


def _nothing_stored(db) -> None:  # noqa: ANN001
    assert _count(db, Referral, Referral.origen_externo.is_not(None)) == 0
    assert _count(db, FhirInboundEvent, FhirInboundEvent.fhir_resource_type == "Bundle") == 0
    assert _count(db, PartnerDiagnosticReport) == 0
    assert _count(db, Observation, Observation.origin == ObservationOrigin.FHIR) == 0


def _bad_observation() -> dict:
    return observation(*DOC, value=5000, resource_id="obs-implausible")


def _draft_report() -> dict:
    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    return {"resourceType": "DiagnosticReport", "status": "preliminary", "subject": {"identifier": fid}}


def _foreign_subject() -> dict:
    other = observation("CC", "1999888777", value=120, resource_id="obs-other")
    return other


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(_bad_observation, id="observation-out-of-range-as-last-entry"),
        pytest.param(_draft_report, id="diagnostic-report-not-final-as-last-entry"),
        pytest.param(_foreign_subject, id="subject-of-another-patient"),
        pytest.param(lambda: {"resourceType": "Encounter", "status": "finished"}, id="resource-not-allowed"),
    ],
)
def test_an_invalid_bundle_is_a_422_with_an_operation_outcome_and_stores_nothing(loop, extra) -> None:
    response = _post(loop, "/interop/Bundle", _hand_bundle(extra()))
    assert response.status_code == 422, response.text
    assert outcome_issues(response)
    _nothing_stored(loop.db)
    (row,) = [r for r in audit_rows(loop.db, AuditAction.INTEROP_REJECTED)
              if r.metadata_json.get("recurso") == "Bundle"]
    assert row.metadata_json["codigo_http"] == 422


def test_a_bundle_needs_exactly_one_patient_and_one_service_request_without_direct_identifiers(loop) -> None:
    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    twice = _hand_bundle()
    twice["entry"].append(deepcopy(twice["entry"][1]))
    no_patient = _hand_bundle()
    del no_patient["entry"][0]
    named = _hand_bundle(patient={"resourceType": "Patient", "identifier": [fid],
                                  "telecom": [{"system": "phone", "value": CANARY["phone"]}]})
    not_a_bundle = {"resourceType": "Observation"}
    for body in (twice, no_patient, named, not_a_bundle):
        assert _post(loop, "/interop/Bundle", body).status_code == 422
    _nothing_stored(loop.db)


def test_a_bundle_for_an_unknown_patient_is_queued_with_a_202(loop) -> None:
    fid = {"system": get_settings().federated_system, "value": federated("CC", "5550001112")}
    bundle = _hand_bundle()
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] == "Patient":
            resource["identifier"] = [fid]
        else:
            resource["subject"] = {"identifier": fid}
    response = _post(loop, "/interop/Bundle", bundle)
    assert response.status_code == 202, response.text
    (queued,) = loop.db.scalars(select(FhirInboundEvent).where(
        FhirInboundEvent.fhir_resource_type == "Bundle")).all()
    assert queued.estado_conciliacion == "pendiente" and queued.motivo_pendiente == "paciente_desconocido"
    assert _count(loop.db, Referral, Referral.origen_externo.is_not(None)) == 0


def test_the_bundle_route_has_the_same_protection_as_the_other_entries(loop) -> None:
    bundle = _hand_bundle()
    assert signed_post(loop.http, "/interop/Bundle", bundle, loop.creds, None).status_code == 401
    token = get_token(loop.http, loop.creds)
    assert signed_post(loop.http, "/interop/Bundle", bundle, loop.creds, token,
                       sign_with="not-the-secret").status_code == 401
    only_obs = register(loop.db, "solo-observaciones", (clientes.SCOPE_OBSERVATION,))
    assert signed_post(loop.http, "/interop/Bundle", bundle, only_obs,
                       get_token(loop.http, only_obs)).status_code == 403
    body = _post(loop, "/interop/Bundle", bundle)
    assert body.status_code == 201 and body.json()["id"] == str(loop.received().id)
    huge = deepcopy(bundle)
    huge["entry"][1]["resource"]["note"] = [{"text": "x" * 300_000}]
    assert _post(loop, "/interop/Bundle", huge).status_code == 413
    # The partner credentials open the interop entries and nothing else.
    app.dependency_overrides.pop(get_current_user, None)
    other = register(loop.db, "otro-socio", ALL_SCOPES)
    bearer = {"Authorization": f"Bearer {get_token(loop.http, other)}"}
    for path in ("/api/v1/patients", f"/api/v1/patients/{loop.patient['id']}/fhir-bundle", OUTBOUND,
                 REFERRALS):
        assert loop.http.get(path, headers=bearer).status_code in (401, 403), path


# -- 4. B answers A; 5. A receives the answer -------------------------------------------------------


def test_accepting_sends_a_task_updates_a_and_tells_the_sending_doctor(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    referral = loop.received()
    response = loop.specialist.post(f"{REFERRALS}/{referral.id}/accept")
    assert response.status_code == 200 and response.json()["estado"] == "aceptada"

    (task,) = loop.wire.of("Task")
    assert task["status"] == "accepted" and "statusReason" not in task
    assert task["focus"]["identifier"]["value"] == sent["solicitud_id"]
    (row,) = loop.outbound()
    assert row["estado"] == "aceptada" and row["id"] == sent["id"]

    (note,) = inbox(loop.db, "operador.norte", "respuesta_socio")
    assert note.data["estado"] == "aceptada" and note.data["cliente"] == SELF
    assert str(loop.patient["id"]) == str(note.patient_id)
    # Both sides leave audit rows with external origin.
    (task_in,) = _interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "Task")
    assert task_in.metadata_json["origen"] == "externo" and task_in.metadata_json["resultado"] == "aceptada"
    (task_out,) = _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Task")
    assert task_out.metadata_json["entregado"] is True and task_out.metadata_json["reintento"] is False
    (outbox,) = loop.db.scalars(select(InteropTaskOutbox)).all()
    assert outbox.delivered_at is not None and outbox.attempts == 1


def test_rejecting_sends_the_reason_back(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    response = loop.specialist.post(f"{REFERRALS}/{referral.id}/reject", json={"motivo": "Sin cupos este mes"})
    assert response.status_code == 200
    (task,) = loop.wire.of("Task")
    assert task["status"] == "rejected" and task["statusReason"]["text"] == "Sin cupos este mes"
    (row,) = loop.outbound()
    assert row["estado"] == "rechazada" and row["resumen_respuesta"] == "Sin cupos este mes"
    (note,) = inbox(loop.db, "operador.norte", "respuesta_socio")
    assert "Sin cupos este mes" in note.message and note.data["estado"] == "rechazada"


def test_a_replayed_task_is_idempotent(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    loop.specialist.post(f"{REFERRALS}/{referral.id}/accept")
    (task,) = loop.wire.of("Task")
    again = loop.wire.sender(loop.db, SELF).enviar_task(task)  # a new nonce, the same answer
    assert again.status == 200 and again.entregado
    assert loop.outbound()[0]["estado"] == "aceptada"
    assert len(inbox(loop.db, "operador.norte", "respuesta_socio")) == 1
    assert len(_interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "Task")) == 1
    # A contradicting answer to a referral that already has one is a conflict, not an overwrite.
    contradiction = {**task, "status": "rejected", "statusReason": {"text": "Cambio de opinion"}}
    assert _post(loop, "/interop/Task", contradiction).status_code == 409
    assert loop.outbound()[0]["estado"] == "aceptada"


def test_another_partner_cannot_answer_a_referral_that_is_not_theirs(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    intruder = register(loop.db, "otro-socio", ALL_SCOPES)
    task = {"resourceType": "Task", "status": "accepted", "intent": "order",
            "focus": {"identifier": {"system": roundtrip.REQUEST_SYSTEM, "value": sent["solicitud_id"]}}}
    response = signed_post(loop.http, "/interop/Task", task, intruder, get_token(loop.http, intruder))
    assert response.status_code == 404
    unknown = {**task, "focus": {"identifier": {"value": "sr-does-not-exist"}}}
    assert _post(loop, "/interop/Task", unknown).status_code == 404
    assert loop.outbound()[0]["estado"] == "enviada"
    assert inbox(loop.db, "operador.norte", "respuesta_socio") == []


@pytest.mark.parametrize(
    "task",
    [
        {"resourceType": "Task", "status": "completed", "focus": {"identifier": {"value": "sr-1"}}},
        {"resourceType": "Task", "status": "accepted"},
        {"resourceType": "Task", "status": "rejected", "focus": {"identifier": {"value": "sr-1"}}},
        {"resourceType": "Bundle"},
    ],
)
def test_an_invalid_task_is_a_422(loop, task) -> None:
    response = _post(loop, "/interop/Task", task)
    assert response.status_code == 422 and outcome_issues(response)


def test_a_network_failure_keeps_the_decision_and_the_outbox_retries_the_task(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    referral = loop.received()
    loop.wire.down = True
    response = loop.specialist.post(f"{REFERRALS}/{referral.id}/accept")
    assert response.status_code == 200 and response.json()["estado"] == "aceptada"  # not rolled back

    loop.db.expire_all()
    (outbox,) = loop.db.scalars(select(InteropTaskOutbox)).all()
    assert outbox.delivered_at is None and outbox.attempts == 1 and outbox.last_error
    assert loop.outbound()[0]["estado"] == "enviada"
    (failure,) = _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Task")
    assert failure.metadata_json["entregado"] is False and failure.metadata_json["reintento"] is False

    assert loop.doctor.post("/api/v1/interop/outbox/reintentar").status_code == 403  # admin only
    loop.wire.down = False
    retry = loop.admin.post("/api/v1/interop/outbox/reintentar")
    assert retry.status_code == 200 and retry.json() == {"entregadas": 1, "fallidas": 0}

    loop.db.expire_all()
    assert loop.db.scalar(select(InteropTaskOutbox)).delivered_at is not None
    assert loop.outbound()[0]["estado"] == "aceptada" and loop.outbound()[0]["id"] == sent["id"]
    retried = [r for r in _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Task")
               if r.metadata_json["reintento"]]
    assert len(retried) == 1 and retried[0].metadata_json["entregado"] is True
    assert len(inbox(loop.db, "operador.norte", "respuesta_socio")) == 1
    assert loop.admin.post("/api/v1/interop/outbox/reintentar").json() == {"entregadas": 0, "fallidas": 0}


def test_a_partner_that_rejects_the_bundle_leaves_only_the_validation_record(loop, monkeypatch) -> None:
    loop.grant_consent()
    real = roundtrip.validate_bundle

    def strict(bundle, client_id):  # noqa: ANN001, ANN202
        from app.services.interop import receptor
        from app.services.interop.outcome import Issue

        raise receptor.Rejection(422, [Issue("value", "Servicio no ofrecido por esta IPS")], "perfil",
                                 client_id=client_id)

    monkeypatch.setattr(roundtrip, "validate_bundle", strict)
    response = loop.refer()
    monkeypatch.setattr(roundtrip, "validate_bundle", real)
    assert response.status_code == 201
    body = response.json()
    assert body["estado"] == "rechazada_por_validacion"
    assert "Servicio no ofrecido por esta IPS" in body["resumen_respuesta"]
    assert loop.received() is None
    assert _count(loop.db, PartnerDiagnosticReport) == 0
    assert _count(loop.db, FhirInboundEvent, FhirInboundEvent.fhir_resource_type == "Bundle") == 0


def test_a_partner_that_cannot_be_reached_keeps_the_record_as_error_envio(loop) -> None:
    # Behaviour changed on purpose. The record used to be saved only AFTER the send, so an
    # answer lost in transit (B committed the Bundle, A timed out) left B with a referral
    # and A with nothing: B's later Task got a 404 forever. Now the record is committed as
    # ``enviando`` BEFORE sending and ends as ``error_envio`` when the transport fails.
    loop.grant_consent()
    loop.wire.down = True
    response = loop.refer()
    assert response.status_code == 502
    assert loop.received() is None
    (failed,) = loop.db.scalars(select(OutboundReferral)).all()
    assert failed.status == roundtrip.STATUS_SEND_ERROR and failed.service_request_id
    (row,) = _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Bundle")
    assert row.metadata_json["entregado"] is False
    loop.wire.down = False
    assert loop.refer().status_code == 201  # the doctor can still try again
    assert _count(loop.db, OutboundReferral) == 2


def _lose_the_answer(loop):  # noqa: ANN001, ANN202
    """A partner that COMMITS the Bundle while A never sees the answer (a read timeout)."""

    honest = loop.wire.sender

    def sender(db, client_id):  # noqa: ANN001, ANN202
        real = honest(db, client_id)

        class Timeout(type(real)):
            def enviar_bundle(self, resource):  # noqa: ANN001, ANN202
                super().enviar_bundle(resource)  # B validates and commits
                return SendResult("Bundle", 0, False, "Sin respuesta del socio: ReadTimeout", 1)

        real.__class__ = Timeout
        return real

    return sender


def test_a_task_for_a_referral_whose_answer_was_lost_still_matches_its_record(loop, monkeypatch) -> None:
    loop.grant_consent()
    monkeypatch.setattr(destinations, "sender_for_partner", _lose_the_answer(loop))
    assert loop.refer().status_code == 502
    (lost,) = loop.db.scalars(select(OutboundReferral)).all()
    assert lost.status == roundtrip.STATUS_SEND_ERROR
    referral = loop.received()  # B did store it
    assert referral is not None and referral.referencia_externa == lost.service_request_id

    monkeypatch.setattr(destinations, "sender_for_partner", loop.wire.sender)
    assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200
    loop.db.expire_all()
    assert loop.db.get(OutboundReferral, lost.id).status == roundtrip.STATUS_ACCEPTED
    (note,) = inbox(loop.db, "operador.norte", "respuesta_socio")
    assert note.data["estado"] == "aceptada"


def test_a_task_that_overtakes_the_sender_is_not_overwritten_by_enviada(loop, monkeypatch) -> None:
    loop.grant_consent()
    honest = loop.wire.sender

    def sender(db, client_id):  # noqa: ANN001, ANN202
        real = honest(db, client_id)

        class Fast(type(real)):
            def enviar_bundle(self, resource):  # noqa: ANN001, ANN202
                result = super().enviar_bundle(resource)
                # B's specialist answers before A has finished its own bookkeeping.
                referral = loop.received()
                assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200
                return result

        real.__class__ = Fast
        return real

    monkeypatch.setattr(destinations, "sender_for_partner", sender)
    response = loop.refer()
    assert response.status_code == 201
    loop.db.expire_all()
    (row,) = loop.db.scalars(select(OutboundReferral)).all()
    assert row.status == roundtrip.STATUS_ACCEPTED and response.json()["estado"] == "aceptada"


def test_a_timeout_whose_task_landed_during_the_send_is_a_success_not_a_502(loop, monkeypatch) -> None:
    # The transport times out, but B committed the Bundle and its Task already reached A: the
    # REAL state of the record is ``aceptada``, so the doctor gets a 201 with that state.
    loop.grant_consent()
    honest = loop.wire.sender

    def sender(db, client_id):  # noqa: ANN001, ANN202
        real = honest(db, client_id)

        class TimeoutAfterAnswer(type(real)):
            def enviar_bundle(self, resource):  # noqa: ANN001, ANN202
                super().enviar_bundle(resource)  # B validates and commits
                referral = loop.received()
                assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200
                return SendResult("Bundle", 0, False, "Sin respuesta del socio: ReadTimeout", 1)

        real.__class__ = TimeoutAfterAnswer
        return real

    monkeypatch.setattr(destinations, "sender_for_partner", sender)
    response = loop.refer()
    assert response.status_code == 201, response.text
    assert response.json()["estado"] == "aceptada"
    loop.db.expire_all()
    (row,) = loop.db.scalars(select(OutboundReferral)).all()
    assert row.status == roundtrip.STATUS_ACCEPTED


def test_the_doctor_only_sees_their_own_outbound_referrals(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    assert len(loop.outbound()) == 1
    assert len(loop.admin.get(OUTBOUND).json()) == 1
    assert loop.specialist.get(OUTBOUND).status_code == 403


# -- Review round: Bundle shape, size, duplicates ----------------------------------------------------


def test_the_partner_bundle_is_a_collection_without_total_and_carries_each_analysis_once(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    (bundle,) = loop.wire.of("Bundle")
    assert bundle["type"] == "collection" and "total" not in bundle  # FHIR bdl-1
    kinds = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert kinds.count("DiagnosticReport") == 1 and "RiskAssessment" not in kinds


def test_a_referral_with_hundreds_of_observations_is_capped_and_the_partner_accepts_it(loop) -> None:
    loop.grant_consent()
    db = loop.db
    patient_id = uuid.UUID(loop.patient["id"])
    encounter_id = db.scalar(select(Observation.encounter_id).where(Observation.patient_id == patient_id))
    now = datetime.now(timezone.utc)
    for i in range(600):
        db.add(Observation(
            patient_id=patient_id, encounter_id=encounter_id, status=ObservationStatus.FINAL,
            code_system="http://loinc.org", code="2339-0", display="Glucosa", value_numeric=90 + i % 40,
            unit="mg/dL", observed_at=now - timedelta(minutes=i + 1),
        ))
    db.commit()

    loop.sent_referral()
    (bundle,) = loop.wire.of("Bundle")
    assert len(bundle["entry"]) <= roundtrip.MAX_ENTRIES - envio.ENTRY_MARGIN
    assert len(encode(bundle)) <= receptor.MAX_BODY_BYTES * envio.BYTES_SHARE
    kinds = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert kinds.count("Patient") == 1 and kinds.count("ServiceRequest") == 1
    assert kinds.count("DiagnosticReport") == 1  # the evidence is never squeezed out
    kept = [e["resource"]["effectiveDateTime"] for e in bundle["entry"]
            if e["resource"]["resourceType"] == "Observation"]
    assert kept == sorted(kept, reverse=True) and len(kept) > 100  # the most recent first
    assert loop.received() is not None  # B validated and stored it


# -- Review round: token answers that are not a token ---------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(200, text="<html>login</html>"), id="html"),
        pytest.param(httpx.Response(200, json={"token_type": "Bearer"}), id="no-access-token"),
        pytest.param(httpx.Response(200, json=["not", "an", "object"]), id="json-list"),
        pytest.param(httpx.Response(200, json={"access_token": "t", "expires_in": "soon"}), id="bad-expiry"),
    ],
)
def test_a_token_answer_that_is_not_a_token_is_an_audited_gateway_error(loop, monkeypatch, answer) -> None:
    loop.grant_consent()

    def sender(_db, _client_id):  # noqa: ANN001, ANN202
        return InteropSender(
            "http://partner.invalid", "x", "y", "z", retries=0, sleep=lambda _s: None,
            http=httpx.Client(transport=httpx.MockTransport(lambda _request: answer)),
        )

    monkeypatch.setattr(destinations, "sender_for_partner", sender)
    response = loop.refer()
    assert response.status_code == 502
    (row,) = _interop_rows(loop.db, AuditAction.INTEROP_SENT, "Bundle")
    assert row.metadata_json["entregado"] is False
    (kept,) = loop.db.scalars(select(OutboundReferral)).all()
    assert kept.status == roundtrip.STATUS_SEND_ERROR


# -- Review round: reconciling a queued Bundle that no longer validates ------------------------------


def test_a_queued_bundle_that_no_longer_validates_is_a_422_when_reconciled(loop) -> None:
    fid = {"system": get_settings().federated_system, "value": federated("CC", "5550001112")}
    bundle = _hand_bundle()
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] == "Patient":
            resource["identifier"] = [fid]
        else:
            resource["subject"] = {"identifier": fid}
    assert _post(loop, "/interop/Bundle", bundle).status_code == 202
    (queued,) = loop.db.scalars(select(FhirInboundEvent).where(
        FhirInboundEvent.fhir_resource_type == "Bundle")).all()
    queued.resource = {**queued.resource, "entry": queued.resource["entry"]
                       + [{"resource": {"resourceType": "Encounter", "status": "finished"}}]}
    loop.db.commit()

    response = loop.admin.post(f"/api/v1/interop/pendientes/{queued.id}/conciliar",
                               json={"paciente_id": loop.patient["id"]})
    assert response.status_code == 422, response.text
    assert "Encounter" in response.json()["detail"]
    loop.db.expire_all()
    assert loop.db.get(FhirInboundEvent, queued.id).estado_conciliacion == "pendiente"
    assert _count(loop.db, Referral, Referral.origen_externo.is_not(None)) == 0


# -- Review round: the answer is sent after the response, from the outbox ---------------------------


def test_the_task_is_queued_before_the_response_and_sent_by_a_background_task(loop, monkeypatch) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    scheduled: list = []
    real_delivery = roundtrip.deliver_queued
    monkeypatch.setattr(roundtrip, "deliver_queued", lambda outbox_id: scheduled.append(outbox_id))
    assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200

    # Decision committed and Task persisted, but nothing travelled inside the request.
    assert loop.wire.of("Task") == []
    (row,) = loop.db.scalars(select(InteropTaskOutbox)).all()
    assert row.delivered_at is None and row.attempts == 0 and scheduled == [row.id]
    # What the background task runs: a session of its own, one attempt, then delivered.
    assert real_delivery(row.id) is True
    assert len(loop.wire.of("Task")) == 1 and loop.outbound()[0]["estado"] == "aceptada"


# -- Review round: permanent failures, attempt cap, sender lifecycle ---------------------------------


def _stale_tasks(loop, count: int, *, attempts: int) -> list[InteropTaskOutbox]:  # noqa: ANN001
    """Undelivered Tasks for referrals the partner does not know (it answers 404)."""

    db = loop.db
    patient = db.get(Patient, uuid.UUID(loop.patient["id"]))
    specialist = _user(db, "especialista.norte")
    rows = []
    for i in range(count):
        referral = referrals_service.create_external(
            db, patient=patient, specialist=specialist, motivo="Control", origen=SELF,
            referencia=f"sr-gone-{i}", idempotency_key=f"{roundtrip.BUNDLE_KEY_PREFIX}stale-{i}")
        task = {"resourceType": "Task", "status": "accepted", "intent": "order",
                "focus": {"identifier": {"system": roundtrip.REQUEST_SYSTEM, "value": f"sr-gone-{i}"}}}
        row = InteropTaskOutbox(referral_id=referral.id, client_id=SELF, resource=task, attempts=attempts,
                                last_error="old failure")
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def test_permanent_failures_do_not_block_a_fresh_task(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    stale = _stale_tasks(loop, 55, attempts=3)
    loop.wire.down = True
    assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200
    (fresh,) = [r for r in loop.db.scalars(select(InteropTaskOutbox)).all() if r.referral_id == referral.id]
    assert fresh.delivered_at is None and fresh.attempts == 1

    loop.wire.down = False
    first = loop.admin.post("/api/v1/interop/outbox/reintentar").json()
    loop.db.expire_all()
    assert loop.db.get(InteropTaskOutbox, fresh.id).delivered_at is not None  # not starved by the 55
    assert first["entregadas"] == 1

    for _ in range(3):  # the stale ones answer 404: permanent, they stop being retried
        loop.admin.post("/api/v1/interop/outbox/reintentar")
    loop.db.expire_all()
    assert roundtrip.pending_outbox(loop.db) == []
    for row in stale:
        assert row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
        assert row.attempts >= roundtrip.MAX_DELIVERY_ATTEMPTS and row.delivered_at is None
    assert loop.admin.post("/api/v1/interop/outbox/reintentar").json() == {"entregadas": 0, "fallidas": 0}


def test_a_transient_failure_is_retried_until_the_attempt_cap(loop) -> None:
    (row,) = _stale_tasks(loop, 1, attempts=roundtrip.MAX_DELIVERY_ATTEMPTS - 1)
    loop.wire.down = True  # status 0: transient, not a 4xx answer
    assert loop.admin.post("/api/v1/interop/outbox/reintentar").json() == {"entregadas": 0, "fallidas": 1}
    loop.db.expire_all()
    row = loop.db.get(InteropTaskOutbox, row.id)
    assert row.attempts == roundtrip.MAX_DELIVERY_ATTEMPTS
    assert not row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
    assert roundtrip.pending_outbox(loop.db) == []


class _RecordingSender:
    def __init__(self, log: list) -> None:
        self.log = log
        self.closed = 0

    def enviar_task(self, resource: dict) -> SendResult:
        self.log.append(("task", self))
        return SendResult("Task", 200, True, "ok", 1)

    def enviar_bundle(self, resource: dict) -> SendResult:
        self.log.append(("bundle", self))
        return SendResult("Bundle", 201, True, "ok", 1)

    def close(self) -> None:
        self.closed += 1


def test_a_retry_batch_reuses_one_sender_per_partner_and_closes_it(loop, monkeypatch) -> None:
    _stale_tasks(loop, 3, attempts=0)
    built: list[_RecordingSender] = []
    log: list = []

    def sender(_db, _client_id):  # noqa: ANN001, ANN202
        built.append(_RecordingSender(log))
        return built[-1]

    monkeypatch.setattr(destinations, "sender_for_partner", sender)
    assert roundtrip.retry_outbox(loop.db) == {"entregadas": 3, "fallidas": 0}
    assert len(built) == 1 and built[0].closed == 1 and len(log) == 3


def test_a_single_delivery_and_a_referral_close_the_sender_they_build(loop, monkeypatch) -> None:
    built: list[_RecordingSender] = []

    def sender(_db, _client_id):  # noqa: ANN001, ANN202
        built.append(_RecordingSender([]))
        return built[-1]

    monkeypatch.setattr(destinations, "sender_for_partner", sender)
    (row,) = _stale_tasks(loop, 1, attempts=0)
    assert roundtrip.deliver_queued(row.id) is True
    assert [s.closed for s in built] == [1]

    loop.grant_consent()
    assert loop.refer().status_code == 201
    assert [s.closed for s in built] == [1, 1]


# -- Round 3: free text that leaves the system is scrubbed --------------------------------------------

IDENTIFYING_TEXT = (
    f"{CANARY['first_name']} {CANARY['last_name']}, CC {CANARY['document_number']}, tel {CANARY['phone']}, "
    f"{CANARY['email']}, vive en {CANARY['address']}"
)
LEAKS = (CANARY["first_name"], CANARY["last_name"], CANARY["document_number"], CANARY["phone"],
         CANARY["email"], "Carrera 77", "88-99")


def test_the_reason_and_the_service_sent_to_the_partner_carry_no_patient_identifier(loop) -> None:
    loop.grant_consent()
    response = loop.refer(motivo=f"Control de diabetes de {IDENTIFYING_TEXT}",
                          servicio=f"Endocrinologia para {CANARY['first_name']}")
    assert response.status_code == 201, response.text
    (bundle,) = loop.wire.of("Bundle")
    order = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "ServiceRequest")
    travelled = json.dumps(order, ensure_ascii=False)
    for leak in LEAKS:
        assert leak not in travelled, leak
    assert "Control de diabetes" in travelled and "[PACIENTE]" in travelled
    assert order["orderDetail"] == [{"text": "Endocrinologia para [PACIENTE]"}]
    # The partner's own copy never held them either.
    assert all(leak not in loop.received().motivo for leak in LEAKS)
    # The sending doctor's local record keeps the wording they typed (our own database).
    (kept,) = loop.db.scalars(select(OutboundReferral)).all()
    assert CANARY["first_name"] in kept.reason


def test_a_bundle_without_a_service_request_scrubs_the_clinician_notes_and_drops_the_signer(loop) -> None:
    from app.models import AiReport

    report = loop.db.get(AiReport, uuid.UUID(loop.approved))
    signer = report.reviewer.full_name
    assert signer
    report.notes = f"Concuerda con la clinica de {IDENTIFYING_TEXT}"
    loop.db.commit()
    loop.grant_consent()

    sent: list[dict] = []

    class Capture:
        def enviar_bundle(self, resource: dict) -> SendResult:
            sent.append(resource)
            return SendResult("Bundle", 201, True, "ok", 1)

    envio.send_to_partner(
        loop.db, actor=_user(loop.db, "operador.norte"), patient_id=uuid.UUID(loop.patient["id"]),
        client_id=SELF, sender=Capture(),
    )
    (bundle,) = sent
    assessments = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "RiskAssessment"]
    assert assessments, "the approved report travels as a RiskAssessment when there is no ServiceRequest"
    travelled = json.dumps(assessments, ensure_ascii=False)
    for leak in LEAKS:
        assert leak not in travelled, leak
    assert "Concuerda con la clinica" in travelled and "[PACIENTE]" in travelled
    assert signer not in travelled and all("performer" not in a for a in assessments)


def test_the_rejection_reason_in_the_task_carries_no_patient_identifier(loop) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    typed = f"No procede: {IDENTIFYING_TEXT}"
    assert loop.specialist.post(f"{REFERRALS}/{referral.id}/reject", json={"motivo": typed}).status_code == 200
    (task,) = loop.wire.of("Task")
    reason = task["statusReason"]["text"]
    for leak in LEAKS:
        assert leak not in reason, leak
    assert reason.startswith("No procede") and "[PACIENTE]" in reason
    (outbound,) = loop.outbound()
    assert outbound["estado"] == "rechazada"
    assert all(leak not in (outbound["resumen_respuesta"] or "") for leak in LEAKS)
    (queued,) = loop.db.scalars(select(InteropTaskOutbox)).all()
    assert all(leak not in json.dumps(queued.resource, ensure_ascii=False) for leak in LEAKS)


# -- Round 3: the status change and its Task row are one transaction ----------------------------------


def test_the_task_row_and_the_status_change_commit_together_or_not_at_all(loop, monkeypatch) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    with monkeypatch.context() as patched:
        def broken(_referral, _patient=None):  # noqa: ANN001, ANN202
            raise RuntimeError("the outbox insert failed")

        patched.setattr(roundtrip, "build_task", broken)
        with pytest.raises(RuntimeError, match="outbox insert failed"):
            loop.specialist.post(f"{REFERRALS}/{referral.id}/accept")

    loop.db.expire_all()
    stored = loop.db.get(Referral, referral.id)
    assert stored.status == ReferralStatus.PENDING and stored.accepted_at is None  # rolled back too
    assert _count(loop.db, InteropTaskOutbox) == 0
    assert audit_rows(loop.db, AuditAction.REFERRAL_ACCEPTED) == []
    assert loop.wire.of("Task") == []
    # Nothing is half-done: the specialist simply tries again and both land together.
    assert loop.specialist.post(f"{REFERRALS}/{referral.id}/accept").status_code == 200
    loop.db.expire_all()
    assert loop.db.get(Referral, referral.id).status == ReferralStatus.ACCEPTED
    (row,) = loop.db.scalars(select(InteropTaskOutbox)).all()
    assert row.delivered_at is not None and loop.outbound()[0]["estado"] == "aceptada"


def test_a_rejection_that_cannot_stage_its_task_is_rolled_back_too(loop, monkeypatch) -> None:
    loop.grant_consent()
    loop.sent_referral()
    referral = loop.received()
    monkeypatch.setattr(roundtrip, "stage_answer", lambda *_a: (_ for _ in ()).throw(RuntimeError("no outbox")))
    with pytest.raises(RuntimeError, match="no outbox"):
        loop.specialist.post(f"{REFERRALS}/{referral.id}/reject", json={"motivo": "Sin cupos"})
    loop.db.expire_all()
    stored = loop.db.get(Referral, referral.id)
    assert stored.status == ReferralStatus.PENDING and stored.motivo_rechazo is None
    assert audit_rows(loop.db, AuditAction.REFERRAL_REJECTED) == []


# -- Round 3: _settle cannot overwrite the partner's answer -------------------------------------------


def test_settle_never_overwrites_an_answer_that_won_the_race(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    row = loop.db.get(OutboundReferral, uuid.UUID(sent["id"]))
    row.status = roundtrip.STATUS_SENDING
    loop.db.commit()
    # The partner's Task lands first (here: straight in the database, bypassing the object we
    # hold). Overwriting with ``enviada`` would clobber the answer.
    loop.db.execute(
        update(OutboundReferral).where(OutboundReferral.id == row.id)
        .values(status=roundtrip.STATUS_ACCEPTED, response_summary="listo")
        .execution_options(synchronize_session=False)
    )
    roundtrip._settle(loop.db, row, roundtrip.STATUS_SENT, None)  # noqa: SLF001
    loop.db.expire_all()
    stored = loop.db.get(OutboundReferral, row.id)
    assert stored.status == roundtrip.STATUS_ACCEPTED and stored.response_summary == "listo"


@pytest.mark.parametrize("answered", [roundtrip.STATUS_ACCEPTED, roundtrip.STATUS_REJECTED])
def test_settle_only_moves_a_record_out_of_enviando_or_error_envio(loop, answered) -> None:
    loop.grant_consent()
    row = loop.db.get(OutboundReferral, uuid.UUID(loop.sent_referral()["id"]))
    for before in (roundtrip.STATUS_SENDING, roundtrip.STATUS_SEND_ERROR):
        row.status = before
        loop.db.commit()
        roundtrip._settle(loop.db, row, roundtrip.STATUS_SENT, None)  # noqa: SLF001
        assert row.status == roundtrip.STATUS_SENT
    row.status = answered
    loop.db.commit()
    for later in (roundtrip.STATUS_SENT, roundtrip.STATUS_SEND_ERROR, roundtrip.STATUS_INVALID):
        roundtrip._settle(loop.db, row, later, "late")  # noqa: SLF001
        assert row.status == answered and row.response_summary != "late"


def test_a_replayed_task_after_the_record_was_settled_is_still_idempotent(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    referral = loop.received()
    loop.specialist.post(f"{REFERRALS}/{referral.id}/accept")
    (task,) = loop.wire.of("Task")
    row = loop.db.get(OutboundReferral, uuid.UUID(sent["id"]))
    roundtrip._settle(loop.db, row, roundtrip.STATUS_SENT, None)  # noqa: SLF001  (a late settle changes nothing)
    assert _post(loop, "/interop/Task", task).status_code == 200  # same answer, same stored state
    loop.db.expire_all()
    assert loop.db.get(OutboundReferral, row.id).status == roundtrip.STATUS_ACCEPTED
    assert len(inbox(loop.db, "operador.norte", "respuesta_socio")) == 1


# -- Round 3: a token failure is retryable, a Task endpoint 4xx is permanent --------------------------


class _Scripted:
    """A sender whose ``enviar_task`` raises or answers as told."""

    def __init__(self, *, raises: Exception | None = None, result: SendResult | None = None) -> None:
        self.raises, self.result = raises, result

    def enviar_task(self, resource: dict) -> SendResult:
        if self.raises is not None:
            raise self.raises
        assert self.result is not None
        return self.result

    def close(self) -> None:
        pass


def _deliver_with(loop, monkeypatch, sender) -> InteropTaskOutbox:  # noqa: ANN001
    (row,) = _stale_tasks(loop, 1, attempts=0)
    monkeypatch.setattr(destinations, "sender_for_partner", lambda _db, _client_id: sender)
    assert roundtrip.deliver_queued(row.id) is False
    loop.db.expire_all()
    return loop.db.get(InteropTaskOutbox, row.id)


@pytest.mark.parametrize("status", [401, 403, 400, 0])
def test_a_failure_of_the_token_endpoint_is_retryable_never_permanent(loop, monkeypatch, status) -> None:
    sender = _Scripted(raises=SenderError(f"No se obtuvo el token ({status} invalid_client)", status))
    row = _deliver_with(loop, monkeypatch, sender)
    assert row.attempts == 1 and row.delivered_at is None
    assert not row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
    assert [r.id for r in roundtrip.pending_outbox(loop.db)] == [row.id]  # still in the retry queue


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_a_4xx_answer_of_the_task_endpoint_is_permanent(loop, monkeypatch, status) -> None:
    row = _deliver_with(loop, monkeypatch, _Scripted(result=SendResult("Task", status, False, "no", 1)))
    assert row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
    assert row.attempts >= roundtrip.MAX_DELIVERY_ATTEMPTS
    assert roundtrip.pending_outbox(loop.db) == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_401_or_403_of_the_task_endpoint_is_retried_up_to_the_cap(loop, monkeypatch, status) -> None:
    # A wrong API key or a missing scope: the partner can fix it, so it is NOT permanent.
    row = _deliver_with(loop, monkeypatch, _Scripted(result=SendResult("Task", status, False, "no", 1)))
    assert not row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
    assert row.attempts == 1 and [r.id for r in roundtrip.pending_outbox(loop.db)] == [row.id]
    row.attempts = roundtrip.MAX_DELIVERY_ATTEMPTS  # the cap still ends the retries
    loop.db.commit()
    assert roundtrip.pending_outbox(loop.db) == []


@pytest.mark.parametrize("status", [408, 429, 500, 502, 0])
def test_timeouts_rate_limits_and_server_errors_of_the_task_endpoint_are_retried(loop, monkeypatch, status) -> None:
    row = _deliver_with(loop, monkeypatch, _Scripted(result=SendResult("Task", status, False, "later", 1)))
    assert not row.last_error.startswith(roundtrip.PERMANENT_PREFIX)
    assert row.attempts == 1 and [r.id for r in roundtrip.pending_outbox(loop.db)] == [row.id]


# -- Round 3: a patient without federated id fails fast ------------------------------------------------


def test_a_patient_without_federated_id_is_refused_before_anything_is_stored_or_sent(loop, monkeypatch) -> None:
    loop.grant_consent()
    loop.db.execute(update(Patient).values(id_federado=None))
    loop.db.commit()
    monkeypatch.setattr(get_settings(), "federated_secret", None)

    response = loop.refer()
    assert response.status_code == 422, response.text
    assert "identificador federado" in response.text
    assert loop.wire.sent == [] and loop.wire.built == 0  # the transport was not even configured
    assert _count(loop.db, OutboundReferral) == 0 and loop.received() is None
    (row,) = audit_rows(loop.db, AuditAction.INTEROP_REJECTED)
    assert row.metadata_json["reason"] == "no_federated_id" and row.metadata_json["direction"] == "outbound"
    assert audit_rows(loop.db, AuditAction.INTEROP_SENT) == []
