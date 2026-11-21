# ruff: noqa: F811
"""Receiver of the partner exchange (slides 28-31): token, signature, profile, patient, reaction.

Everything is faked: no network. The partner is a ``TestClient`` with its own credentials;
the application runs on the in-memory database of the seed.
"""

from __future__ import annotations

import time
import uuid

import pytest
from jose import jwt
from sqlalchemy import func, select, update
from starlette.testclient import TestClient

from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token
from app.events.channel import registry
from app.main import app
from app.models import (
    Alert,
    AuditLog,
    FhirInboundEvent,
    Notification,
    Observation,
    Organization,
    Patient,
    Referral,
)
from app.models.enums import AlertStatus, AuditAction, FhirInboundOutcome, ObservationOrigin
from app.services.interop import clientes, firma
from tests.interop_helpers import (
    BOTH,
    FEDERATED_SECRET,
    federated,
    get_token,
    observation,
    outcome_issues,
    referral,
    register,
    signed_post,
    token_request,
)
from tests.test_ai_reports import CANARY, _create_patient  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

OBS = "/interop/Observation"
SR = "/interop/ServiceRequest"
DOC = (CANARY["document_type"], CANARY["document_number"])


class Scene:
    def __init__(self, db, client_as, http, creds, patient) -> None:  # noqa: ANN001
        self.db, self.client_as, self.http, self.creds, self.patient = db, client_as, http, creds, patient
        self.token = get_token(http, creds)

    def obs(self, value: float = 182, **kw):  # noqa: ANN003, ANN201
        return signed_post(self.http, OBS, observation(*DOC, value=value, **kw), self.creds, self.token)

    def inbox(self, username: str) -> list[dict]:
        return self.client_as(username).get("/api/v1/notifications").json()


@pytest.fixture
def scene(db, client_as, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    patient = _create_patient(db, client_as, with_vitals=False)
    creds = register(db)
    yield Scene(db, client_as, TestClient(app), creds, patient)


def _audit(db, action: AuditAction) -> list[AuditLog]:  # noqa: ANN001
    db.expire_all()
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.seq)))


# -- OAuth2 token -------------------------------------------------------------------------


def test_the_token_carries_only_the_registered_scopes_even_if_more_are_requested(db, scene) -> None:
    only_obs = register(db, "equipo2", ("system/Observation.write",))
    response = token_request(scene.http, only_obs, "system/Observation.write system/ServiceRequest.write")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "system/Observation.write" and body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert response.headers["cache-control"] == "no-store"


def test_asking_only_for_an_unregistered_scope_is_invalid_scope(db, scene) -> None:
    only_obs = register(db, "equipo2", ("system/Observation.write",))
    response = token_request(scene.http, only_obs, "system/ServiceRequest.write")
    assert response.status_code == 400 and response.json()["error"] == "invalid_scope"


def test_no_scope_asked_grants_every_registered_scope(scene) -> None:
    assert token_request(scene.http, scene.creds).json()["scope"] == " ".join(BOTH)


def test_a_wrong_secret_or_unknown_client_is_the_same_401(scene) -> None:
    wrong = token_request(scene.http, scene.creds, client_secret="nope")
    unknown = token_request(scene.http, scene.creds, client_id="ghost")
    for response in (wrong, unknown):
        assert response.status_code == 401 and response.json()["error"] == "invalid_client"
    assert wrong.json() == unknown.json()


def test_an_unsupported_grant_type_is_refused(scene) -> None:
    response = token_request(scene.http, scene.creds, grant_type="password")
    assert response.status_code == 400 and response.json()["error"] == "unsupported_grant_type"


def test_json_bodies_and_http_basic_are_also_accepted(scene) -> None:
    creds = scene.creds
    as_json = scene.http.post("/oauth/token", json={
        "grant_type": "client_credentials", "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    })
    assert as_json.status_code == 200
    basic = scene.http.post(
        "/oauth/token", data={"grant_type": "client_credentials"},
        auth=(creds.client_id, creds.client_secret),
    )
    assert basic.status_code == 200


def test_the_token_is_a_system_jwt_apart_from_the_user_one(scene) -> None:
    settings = get_settings()
    claims = jwt.decode(scene.token, settings.interop_signing_key, algorithms=["HS256"],
                        audience=settings.interop_audience, issuer=settings.jwt_issuer)
    assert claims["sub"] == "equipo1" and claims["aud"] == "saludred-interop"
    assert claims["exp"] - claims["iat"] == 600
    assert claims["scope"] == " ".join(BOTH)
    # The user-token decoder refuses it (other audience) and vice versa.
    assert decode_access_token(scene.token) is None
    user_token = create_access_token(_user(scene.db, "admin").id, "ADMIN")
    assert clientes.decode_system_token(user_token) is None


def test_a_disabled_client_gets_no_token_and_its_old_token_stops_working(db, scene) -> None:
    client = clientes.get_active_client(db, "equipo1")
    client.is_active = False
    db.commit()
    assert token_request(scene.http, scene.creds).status_code == 401
    assert scene.obs().status_code == 401


def test_a_failed_token_request_is_audited(db, scene) -> None:
    token_request(scene.http, scene.creds, client_secret="nope")
    row = _audit(db, AuditAction.INTEROP_REJECTED)[-1]
    assert row.metadata_json["recurso"] == "oauth/token" and row.metadata_json["codigo_http"] == 401
    assert row.metadata_json["cliente"] == "equipo1" and row.metadata_json["origen"] == "externo"
    assert "nope" not in str(row.metadata_json)


def test_the_client_id_is_clipped_in_the_audit_row(db, scene) -> None:
    token_request(scene.http, scene.creds, client_id="x" * 300, client_secret="nope")
    row = _audit(db, AuditAction.INTEROP_REJECTED)[-1]
    assert row.metadata_json["cliente"] == "x" * 80


def test_unauthenticated_rejections_are_one_audit_row_per_ip_and_reason_per_minute(
    db, scene, monkeypatch, caplog
) -> None:
    from app.services.interop import receptor

    clock = [1000.0]
    monkeypatch.setattr(receptor, "_now", lambda: clock[0])
    with caplog.at_level("WARNING", logger="app.security"):
        for _ in range(5):
            signed_post(scene.http, OBS, observation(*DOC), scene.creds, None)  # sin_token
        for _ in range(3):
            token_request(scene.http, scene.creds, client_secret="nope")  # invalid_client
    rows = _audit(db, AuditAction.INTEROP_REJECTED)
    assert sorted(r.metadata_json["motivo"] for r in rows) == ["invalid_client", "sin_token"]
    # The rest still leave a trace, in the security log only.
    logged = [r for r in caplog.records if "interop_rechazo" in r.getMessage()]
    assert len(logged) == 8 and all("ip=testclient" in r.getMessage() for r in logged)
    clock[0] += receptor.UNAUTH_AUDIT_WINDOW + 1  # a new minute: one more row per reason
    signed_post(scene.http, OBS, observation(*DOC), scene.creds, None)
    assert len(_audit(db, AuditAction.INTEROP_REJECTED)) == 3


def test_rejections_of_an_authenticated_client_are_never_aggregated(db, scene) -> None:
    for _ in range(3):
        signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token, sign_with="bad")
    assert len(_audit(db, AuditAction.INTEROP_REJECTED)) == 3


# -- Bounded bodies (finding 8) ----------------------------------------------------------------

FORM = {"content-type": "application/x-www-form-urlencoded"}


def test_a_token_request_over_16_kb_is_413_before_it_is_read(scene) -> None:
    response = scene.http.post("/oauth/token", content=b"a=" + b"x" * 20_000, headers=FORM)
    assert response.status_code == 413 and response.json()["error"] == "invalid_request"
    assert response.headers["cache-control"] == "no-store"


def test_a_chunked_token_body_without_content_length_is_cut_off(scene) -> None:
    def chunks():  # noqa: ANN202
        for _ in range(40):
            yield b"x" * 1_000

    response = scene.http.post("/oauth/token", content=chunks(), headers=FORM)
    assert response.status_code == 413


def test_an_interop_chunked_body_over_the_limit_is_413(scene) -> None:
    def chunks():  # noqa: ANN202
        for _ in range(300):
            yield b"x" * 1_000

    response = scene.http.post(
        OBS, content=chunks(), headers={"authorization": f"Bearer {scene.token}"}
    )
    assert response.status_code == 413


# -- 1. token: 401 and 403 ----------------------------------------------------------------


def test_no_token_or_a_garbage_token_is_401(scene) -> None:
    resource = observation(*DOC)
    assert signed_post(scene.http, OBS, resource, scene.creds, None).status_code == 401
    response = signed_post(scene.http, OBS, resource, scene.creds, "not-a-jwt")
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert outcome_issues(response)[0]["severity"] == "error"


def test_a_user_token_is_not_a_system_token(scene) -> None:
    token = create_access_token(_user(scene.db, "admin").id, "ADMIN")
    assert signed_post(scene.http, OBS, observation(*DOC), scene.creds, token).status_code == 401


def test_a_system_token_is_refused_on_user_routes(db) -> None:
    """No ``client_as`` here: the real ``get_current_user`` decides."""

    app.dependency_overrides[get_db] = lambda: db
    try:
        creds = register(db)
        http = TestClient(app)
        token = get_token(http, creds)
        for path in ("/api/v1/patients", "/api/v1/notifications", "/api/v1/audit-logs/head"):
            assert http.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401, path
    finally:
        app.dependency_overrides.clear()


def test_a_valid_token_without_the_scope_is_403(db, scene) -> None:
    only_obs = register(db, "equipo2", ("system/Observation.write",))
    token = get_token(scene.http, only_obs)
    response = signed_post(scene.http, SR, referral(*DOC), only_obs, token)
    assert response.status_code == 403
    assert "system/ServiceRequest.write" in outcome_issues(response)[0]["diagnostics"]
    assert signed_post(scene.http, OBS, observation(*DOC), only_obs, token).status_code == 201


def test_a_scope_removed_after_the_token_was_issued_stops_working_at_once(db, scene) -> None:
    client = clientes.get_active_client(db, "equipo1")
    client.scopes = ["system/ServiceRequest.write"]
    db.commit()
    assert scene.obs().status_code == 403


# -- 2. signature: 401 and 409 ------------------------------------------------------------


def test_a_correctly_signed_message_is_accepted(scene) -> None:
    assert scene.obs().status_code == 201


def test_a_body_changed_after_signing_is_401(scene) -> None:
    from app.services.interop.cliente import encode

    signed = encode(observation(*DOC, value=182))
    tampered = signed.replace(b"182", b"82")
    response = signed_post(scene.http, OBS, signed, scene.creds, scene.token, send_body=tampered)
    assert response.status_code == 401 and "Firma invalida" in outcome_issues(response)[0]["diagnostics"]


def test_a_message_signed_an_hour_ago_is_401(scene) -> None:
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token,
                           now=time.time() - 3600)
    assert response.status_code == 401 and "ventana" in outcome_issues(response)[0]["diagnostics"]


def test_a_future_date_outside_the_window_is_401(scene) -> None:
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token,
                           now=time.time() + 3600)
    assert response.status_code == 401


def test_the_wrong_signing_secret_is_401(scene) -> None:
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token,
                           sign_with="not-the-signing-secret")
    assert response.status_code == 401


def test_the_client_secret_is_not_the_signing_secret(scene) -> None:
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token,
                           sign_with=scene.creds.client_secret)
    assert response.status_code == 401


@pytest.mark.parametrize("header", [firma.HEADER_DATE, firma.HEADER_NONCE, firma.HEADER_SIGNATURE])
def test_a_missing_signature_header_is_401(scene, header) -> None:
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token, drop=(header,))
    assert response.status_code == 401 and header in outcome_issues(response)[0]["diagnostics"]


def test_the_same_message_twice_is_409_the_second_time(db, scene) -> None:
    resource = observation(*DOC)
    first = signed_post(scene.http, OBS, resource, scene.creds, scene.token, nonce="nonce-0001-aaaa")
    replay = signed_post(scene.http, OBS, resource, scene.creds, scene.token, nonce="nonce-0001-aaaa")
    assert (first.status_code, replay.status_code) == (201, 409)
    assert "repetido" in outcome_issues(replay)[0]["diagnostics"]
    assert db.scalar(select(func.count()).select_from(Observation).where(
        Observation.origin == ObservationOrigin.FHIR)) == 1


def test_a_nonce_is_per_client_and_old_nonces_are_purged(db, scene) -> None:
    other = register(db, "equipo2")
    other_token = get_token(scene.http, other)
    a = signed_post(scene.http, OBS, observation(*DOC, resource_id="a"), scene.creds, scene.token,
                    nonce="shared-nonce-01")
    b = signed_post(scene.http, OBS, observation(*DOC, resource_id="b"), other, other_token,
                    nonce="shared-nonce-01")
    assert (a.status_code, b.status_code) == (201, 201)
    from datetime import datetime, timedelta, timezone

    from app.models.interop import InteropNonce

    stale = db.scalar(select(InteropNonce).where(InteropNonce.client_id == "equipo1"))
    stale.seen_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    signed_post(scene.http, OBS, observation(*DOC, resource_id="c"), scene.creds, scene.token)
    assert db.scalar(select(InteropNonce).where(InteropNonce.nonce == "shared-nonce-01",
                                                InteropNonce.client_id == "equipo1")) is None


def test_a_resource_already_received_is_409_even_with_a_new_nonce(scene) -> None:
    resource = observation(*DOC, resource_id="same-id")
    assert signed_post(scene.http, OBS, resource, scene.creds, scene.token).status_code == 201
    again = signed_post(scene.http, OBS, resource, scene.creds, scene.token)
    assert again.status_code == 409 and "ya recibido" in outcome_issues(again)[0]["diagnostics"]


def test_two_partners_may_use_the_same_resource_id(db, scene) -> None:
    other = register(db, "equipo2")
    other_token = get_token(scene.http, other)
    resource = observation(*DOC, resource_id="obs-1")
    assert signed_post(scene.http, OBS, resource, scene.creds, scene.token).status_code == 201
    assert signed_post(scene.http, OBS, resource, other, other_token).status_code == 201


# -- order of the controls -----------------------------------------------------------------


def test_an_invalid_body_without_token_is_401_not_422(scene) -> None:
    junk = b'{"resourceType":"Observation","code":"nonsense"'
    assert signed_post(scene.http, OBS, junk, scene.creds, None).status_code == 401


def test_an_invalid_body_with_a_bad_signature_is_401_not_422(scene) -> None:
    junk = b"not json at all"
    response = signed_post(scene.http, OBS, junk, scene.creds, scene.token, sign_with="wrong-secret")
    assert response.status_code == 401


def test_an_invalid_body_with_a_good_token_and_signature_is_422(scene) -> None:
    response = signed_post(scene.http, OBS, b"not json at all", scene.creds, scene.token)
    assert response.status_code == 422 and outcome_issues(response)[0]["code"] == "structure"


def test_the_body_is_not_parsed_before_the_signature_is_checked(scene, monkeypatch) -> None:
    from app.services.interop import receptor

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("parsed before the signature was verified")

    monkeypatch.setattr(receptor, "parse_and_validate", boom)
    response = signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token,
                           sign_with="wrong-secret")
    assert response.status_code == 401


def test_an_oversized_body_is_413_after_the_token(scene) -> None:
    big = b"x" * 300_000
    assert signed_post(scene.http, OBS, big, scene.creds, scene.token).status_code == 413
    assert signed_post(scene.http, OBS, big, scene.creds, None).status_code == 401


# -- 3. profile: 422 with an OperationOutcome ----------------------------------------------


def _post(scene, resource, path=OBS):  # noqa: ANN001, ANN202
    return signed_post(scene.http, path, resource, scene.creds, scene.token)


def test_a_misspelled_loinc_system_is_422_with_an_operation_outcome(scene) -> None:
    resource = observation(*DOC)
    resource["code"]["coding"][0]["system"] = "http://loinc.com"
    response = _post(scene, resource)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/fhir+json")
    (issue,) = outcome_issues(response)
    assert issue["severity"] == "error" and issue["code"] == "code-invalid"
    assert issue["expression"] == ["Observation.code.coding[0].system"]
    assert "diagnostics" in issue


def test_a_wrong_unit_is_422(scene) -> None:
    resource = observation(*DOC)
    resource["valueQuantity"]["code"] = "mmol/L"
    assert outcome_issues(_post(scene, resource))[0]["expression"] == ["Observation.valueQuantity.code"]


def test_an_implausible_value_is_422(scene) -> None:
    response = _post(scene, observation(*DOC, value=5000))
    assert response.status_code == 422 and "implausible" in outcome_issues(response)[0]["diagnostics"]


def test_a_code_outside_the_profile_is_422(scene) -> None:
    resource = observation(*DOC)
    resource["code"]["coding"][0]["code"] = "8867-4"
    assert _post(scene, resource).status_code == 422


def test_a_service_request_without_snomed_3457005_is_422(scene) -> None:
    resource = referral(*DOC)
    resource["code"]["coding"] = []
    response = _post(scene, resource, SR)
    assert response.status_code == 422 and "3457005" in outcome_issues(response)[0]["diagnostics"]


def test_a_resource_of_the_wrong_type_for_the_route_is_422(scene) -> None:
    assert _post(scene, referral(*DOC), OBS).status_code == 422
    assert _post(scene, observation(*DOC), SR).status_code == 422


def test_a_rejected_profile_message_stores_nothing(db, scene) -> None:
    _post(scene, observation(*DOC, value=5000))
    assert db.scalar(select(func.count()).select_from(FhirInboundEvent)) == 0


# -- 4. patient: 201 -----------------------------------------------------------------------


def test_the_federated_id_is_filled_when_a_patient_is_created_or_edited(db, scene) -> None:
    row = db.get(Patient, uuid.UUID(scene.patient["id"]))
    assert row.id_federado == federated(*DOC)
    row.document_number = "7788990099"
    db.commit()
    assert row.id_federado == federated("CC", "7788990099")


def test_without_the_secret_no_federated_id_is_stored(db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "federated_secret", None)
    assert all(p.id_federado is None for p in db.scalars(select(Patient)))


def test_a_known_patient_by_the_indexed_column_is_201_and_everything_reacts(db, scene) -> None:
    response = scene.obs()
    assert response.status_code == 201
    assert outcome_issues(response)[0]["severity"] == "information"

    row = db.scalar(select(Observation).where(Observation.origin == ObservationOrigin.FHIR))
    assert (row.code, row.code_system, float(row.value_numeric), row.unit) == (
        "2339-0", "http://loinc.org", 182.0, "mg/dL")
    assert str(row.patient_id) == scene.patient["id"]
    assert str(row.encounter_id) == scene.patient["encounter_id"] and row.created_by is None

    event = db.scalar(select(FhirInboundEvent))
    assert event.outcome == FhirInboundOutcome.PROCESSED and event.origen_externo == "equipo1"
    assert event.estado_conciliacion is None and event.id_federado_recibido == federated(*DOC)

    inbox = scene.inbox("operador.norte")
    note = next(n for n in inbox if n["tipo"] == "interop_observacion")
    assert note["mensaje"] == "Nuevo resultado de laboratorio desde equipo1: Glucosa 182 mg/dL"
    assert note["datos"]["origen"] == "externo" and note["paciente_id"] == scene.patient["id"]


def test_a_patient_whose_column_is_null_is_resolved_by_computing(db, scene) -> None:
    db.execute(update(Patient).values(id_federado=None))
    db.commit()
    db.expire_all()
    assert db.get(Patient, uuid.UUID(scene.patient["id"])).id_federado is None
    assert scene.obs().status_code == 201
    assert db.scalar(select(func.count()).select_from(Observation).where(
        Observation.patient_id == uuid.UUID(scene.patient["id"]),
        Observation.origin == ObservationOrigin.FHIR)) == 1


def test_a_patient_with_an_encrypted_document_is_found(db, scene, field_keys) -> None:
    """The document is encrypted at rest; the id is computed from the plain property."""

    db.execute(update(Patient).values(id_federado=None))
    db.commit()
    patient = db.get(Patient, uuid.UUID(scene.patient["id"]))
    patient.document_number = "7788990011"  # sealed with the keys now
    db.execute(update(Patient).values(id_federado=None))
    db.commit()
    db.expire_all()
    assert db.get(Patient, uuid.UUID(scene.patient["id"]))._document_number.startswith("v1.")
    assert scene.obs().status_code == 201


def test_every_message_is_audited_with_the_external_origin(db, scene) -> None:
    scene.obs()
    (row,) = _audit(db, AuditAction.INTEROP_RECEIVED)
    meta = row.metadata_json
    assert meta["origen"] == "externo" and meta["cliente"] == "equipo1"
    assert meta["codigo_http"] == 201 and meta["recurso"] == "Observation"
    assert meta["ip"] == "testclient" and row.ip_address == "testclient"
    assert meta["paciente_id"] == scene.patient["id"]


def test_every_rejection_is_audited_with_client_ip_and_status(db, scene) -> None:
    signed_post(scene.http, OBS, observation(*DOC), scene.creds, None)  # 401, client unknown yet
    signed_post(scene.http, OBS, observation(*DOC), scene.creds, scene.token, sign_with="bad")  # 401
    _post(scene, observation(*DOC, value=5000))  # 422
    rows = _audit(db, AuditAction.INTEROP_REJECTED)
    assert [r.metadata_json["codigo_http"] for r in rows] == [401, 401, 422]
    assert [r.metadata_json["cliente"] for r in rows] == [None, "equipo1", "equipo1"]
    assert all(r.metadata_json["origen"] == "externo" and r.ip_address == "testclient" for r in rows)
    assert [r.metadata_json["motivo"] for r in rows] == ["sin_token", "firma_invalida", "perfil"]


def test_without_the_federated_secret_the_receiver_answers_503_and_audits(db, scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "federated_secret", None)
    response = scene.obs()
    assert response.status_code == 503
    assert "FEDERATED_SECRET" in outcome_issues(response)[0]["diagnostics"]
    assert _audit(db, AuditAction.INTEROP_REJECTED)[-1].metadata_json["codigo_http"] == 503


# -- 4. patient: 202 and the reconciliation queue -------------------------------------------


def _unknown(scene, value: float = 182, **kw):  # noqa: ANN001, ANN003, ANN202
    return signed_post(scene.http, OBS, observation("CC", "5550001112", value=value, **kw),
                       scene.creds, scene.token)


def test_an_unknown_patient_is_202_kept_in_the_queue_and_the_admin_is_told(db, scene) -> None:
    response = _unknown(scene)
    assert response.status_code == 202
    assert "pendiente de conciliacion" in outcome_issues(response)[0]["diagnostics"]

    event = db.scalar(select(FhirInboundEvent))
    assert event.outcome == FhirInboundOutcome.UNKNOWN_PATIENT and event.patient_id is None
    assert event.estado_conciliacion == "pendiente" and event.motivo_pendiente == "paciente_desconocido"
    assert event.resource["valueQuantity"]["value"] == 182  # nothing is lost
    assert db.scalar(select(func.count()).select_from(Observation).where(
        Observation.origin == ObservationOrigin.FHIR)) == 0

    note = next(n for n in scene.inbox("admin") if n["tipo"] == "interop_pendiente")
    assert "pendiente de conciliacion" in note["mensaje"] and note["datos"]["cliente"] == "equipo1"
    assert _audit(db, AuditAction.INTEROP_RECEIVED)[-1].metadata_json["codigo_http"] == 202


def test_the_admin_reconciles_and_the_202_becomes_the_201_path(db, scene) -> None:
    assert _unknown(scene, value=310).status_code == 202
    admin = scene.client_as("admin")

    pending = admin.get("/api/v1/interop/pendientes").json()
    assert len(pending) == 1
    item = pending[0]
    assert item["recurso"] == "Observation" and item["cliente"] == "equipo1"
    assert item["motivo"] == "paciente_desconocido" and item["resumen"] == "Glucosa 310 mg/dL"

    response = admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar",
                          json={"paciente_id": scene.patient["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["estado"] == "conciliado" and response.json()["paciente_id"] == scene.patient["id"]

    db.expire_all()
    row = db.scalar(select(Observation).where(Observation.origin == ObservationOrigin.FHIR))
    assert str(row.patient_id) == scene.patient["id"] and float(row.value_numeric) == 310.0
    event = db.scalar(select(FhirInboundEvent))
    assert event.outcome == FhirInboundOutcome.PROCESSED and event.estado_conciliacion == "conciliado"
    assert event.conciliado_por == _user(db, "admin").id and event.conciliado_en is not None
    assert _audit(db, AuditAction.PATIENT_RECONCILED)[-1].metadata_json["paciente_id"] == scene.patient["id"]
    # The reaction of the 201 path ran: the clinician is told and the critical alert exists.
    assert any(n["tipo"] == "interop_observacion" for n in scene.inbox("operador.norte"))
    alert = db.scalar(select(Alert))
    assert alert is not None and alert.status == AlertStatus.ACTIVE

    admin = scene.client_as("admin")  # the inbox calls above switched the signed-in user
    assert admin.get("/api/v1/interop/pendientes").json() == []
    again = admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar",
                       json={"paciente_id": scene.patient["id"]})
    assert again.status_code == 409


def test_reconciling_needs_a_patient_and_only_the_admin_may_do_it(scene) -> None:
    _unknown(scene)
    event_id = scene.db.scalar(select(FhirInboundEvent.id))
    assert scene.client_as("admin").post(
        f"/api/v1/interop/pendientes/{event_id}/conciliar", json={}).status_code == 409
    for username in ("operador.norte", "especialista.norte"):
        client = scene.client_as(username)
        assert client.get("/api/v1/interop/pendientes").status_code == 403
        assert client.post(f"/api/v1/interop/pendientes/{event_id}/conciliar",
                           json={"paciente_id": scene.patient["id"]}).status_code == 403
        assert client.get("/api/v1/interop/clientes").status_code == 403


def test_the_admin_lists_the_partners_without_any_secret(scene) -> None:
    response = scene.client_as("admin").get("/api/v1/interop/clientes")
    assert response.status_code == 200
    (item,) = response.json()
    assert item["client_id"] == "equipo1" and item["alcances"] == list(BOTH) and item["activo"] is True
    assert not ({"secret_hash", "client_secret", "signing_secret", "secreto_firma"} & item.keys())
    assert scene.creds.client_secret not in response.text and scene.creds.signing_secret not in response.text


# -- 5. reaction: critical value -------------------------------------------------------------


def test_a_critical_glucose_raises_the_critical_alert_and_a_live_event(db, scene) -> None:
    doctor = _user(db, "operador.norte")
    registry.clear()
    with TestClient(app) as http:
        token = create_access_token(doctor.id, doctor.role.code.value)
        with http.websocket_connect(f"/api/v1/events/ws?token={token}") as ws:
            deadline = time.monotonic() + 3
            while registry.connection_count(doctor.id) != 1:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert scene.obs(310).status_code == 201
            registry.push(doctor.id, {"tipo": "marcador", "mensaje": "m"})
            seen = []
            while True:
                frame = ws.receive_json()
                if frame["tipo"] == "marcador":
                    break
                seen.append(frame)
    registry.clear()

    tipos = {f["tipo"] for f in seen}
    assert {"interop_observacion", "alerta_critica"} <= tipos
    live = next(f for f in seen if f["tipo"] == "interop_observacion")
    assert live["datos"]["critico"] is True and live["mensaje"].endswith("Glucosa 310 mg/dL")

    db.expire_all()
    alert = db.scalar(select(Alert))
    assert alert.ai_report_id is None and alert.nivel == "critico" and alert.status == AlertStatus.ACTIVE
    assert str(alert.patient_id) == scene.patient["id"] and alert.recipient_user_id == doctor.id
    assert alert.datos["origen"] == "externo" and alert.datos["cliente"] == "equipo1"
    assert alert.datos["valor"] == 310
    assert db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.ALERT_CREATED)) == 1


def test_a_second_critical_value_does_not_create_a_second_alert(db, scene) -> None:
    assert scene.obs(310, resource_id="a").status_code == 201
    assert scene.obs(350, resource_id="b").status_code == 201
    assert db.scalar(select(func.count()).select_from(Alert)) == 1


@pytest.mark.parametrize(("value", "critical"), [(182, False), (299, False), (300, True), (54, True), (55, False)])
def test_the_thresholds_of_the_critical_glucose(db, scene, value, critical) -> None:
    assert scene.obs(value).status_code == 201
    assert (db.scalar(select(func.count()).select_from(Alert)) == 1) is critical


def test_the_critical_thresholds_are_configurable(db, scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "interop_glucose_critical_high", 200.0)
    scene.obs(250)
    assert db.scalar(select(func.count()).select_from(Alert)) == 1


def test_only_glucose_is_judged_for_the_critical_alert(db, scene) -> None:
    assert scene.obs(190, code="8480-6").status_code == 201  # a high systolic pressure
    assert db.scalar(select(func.count()).select_from(Alert)) == 0


@pytest.mark.parametrize("status", ["cancelled", "entered-in-error"])
def test_a_withdrawn_critical_result_raises_no_alert_and_no_notification(db, scene, status) -> None:
    notifications_before = db.scalar(select(func.count()).select_from(Notification))
    resource = observation(*DOC, value=350)
    resource["status"] = status
    response = signed_post(scene.http, OBS, resource, scene.creds, scene.token)
    assert response.status_code == 201
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Alert)) == 0
    assert db.scalar(select(func.count()).select_from(Notification)) == notifications_before
    # Kept for the record, flagged as cancelled; the control: the same value as final alerts.
    assert db.scalar(select(Observation).where(Observation.origin == ObservationOrigin.FHIR)) is not None
    assert scene.obs(350, resource_id="final-one").status_code == 201
    assert db.scalar(select(func.count()).select_from(Alert)) == 1


def test_queue_maps_only_an_integrity_error_to_the_409() -> None:
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError, OperationalError

    from app.services.interop import receptor

    class FlushFails:
        def __init__(self, error: Exception) -> None:
            self.error, self.rolled_back = error, False

        def add(self, _record) -> None:  # noqa: ANN001
            pass

        def flush(self) -> None:
            raise self.error

        def rollback(self) -> None:
            self.rolled_back = True

    record = SimpleNamespace(origen_externo="equipo1")
    kwargs = {"ip": None, "resource_type": "Observation", "what": "Un resultado"}
    duplicate = FlushFails(IntegrityError("INSERT", {}, Exception("unique")))
    with pytest.raises(receptor.Rejection) as caught:
        receptor._queue(duplicate, record, **kwargs)
    assert caught.value.status == 409 and duplicate.rolled_back
    with pytest.raises(OperationalError):  # a dead connection is a 5xx, not a "duplicate"
        receptor._queue(FlushFails(OperationalError("INSERT", {}, Exception("gone"))), record, **kwargs)


def test_reconcile_locks_the_event_row_on_postgresql() -> None:
    from sqlalchemy.dialects import postgresql

    from app.services.interop import receptor

    sql = str(receptor.locked_event_stmt(uuid.uuid4()).compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


# -- 5. reaction: referral ---------------------------------------------------------------------


def _org_code(db, username: str) -> str:  # noqa: ANN001
    return db.get(Organization, _user(db, username).organization_id).code


def test_a_referral_lands_in_the_inbox_of_the_specialist_of_the_target_ips(db, scene) -> None:
    code = _org_code(db, "especialista.norte")
    response = signed_post(scene.http, SR, referral(*DOC, organization_code=code, resource_id="r-1"),
                           scene.creds, scene.token)
    assert response.status_code == 201, response.text

    row = db.scalar(select(Referral))
    specialist = _user(db, "especialista.norte")
    assert row.specialist_user_id == specialist.id and row.referring_user_id is None
    assert row.origen_externo == "equipo1" and row.referencia_externa == "r-1"
    assert row.motivo == "Control de diabetes tipo 2" and str(row.patient_id) == scene.patient["id"]

    received = scene.client_as("especialista.norte").get("/api/v1/referrals").json()
    assert [r["id"] for r in received] == [str(row.id)]
    assert received[0]["remitente_id"] is None
    assert received[0]["remitente_nombre"] == "Sistema externo: equipo1"
    note = next(n for n in scene.inbox("especialista.norte") if n["tipo"] == "remision_creada")
    assert "equipo1" in note["mensaje"] and note["datos"]["origen_externo"] == "equipo1"
    # The specialist may now open the record (pending referral grants access).
    assert scene.client_as("especialista.norte").get(
        f"/api/v1/patients/{scene.patient['id']}").status_code == 200

    audit = _audit(db, AuditAction.INTEROP_RECEIVED)[-1]
    assert audit.metadata_json["origen"] == "externo" and audit.metadata_json["remision_id"] == str(row.id)
    created = _audit(db, AuditAction.REFERRAL_CREATED)[-1]
    assert created.metadata_json["origen"] == "externo" and created.metadata_json["cliente"] == "equipo1"


def test_the_specialist_can_answer_an_external_referral(db, scene) -> None:
    code = _org_code(db, "especialista.norte")
    signed_post(scene.http, SR, referral(*DOC, organization_code=code), scene.creds, scene.token)
    referral_id = db.scalar(select(Referral.id))
    client = scene.client_as("especialista.norte")
    assert client.post(f"/api/v1/referrals/{referral_id}/accept").status_code == 200
    done = client.post(f"/api/v1/referrals/{referral_id}/report", json={"informe": "Control hecho"})
    assert done.status_code == 200 and done.json()["remitente_nombre"] == "Sistema externo: equipo1"


def test_an_unknown_ips_falls_back_to_the_default_specialist(db, scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "interop_default_specialist", "especialista.norte")
    response = signed_post(scene.http, SR, referral(*DOC, organization_code="NO-SUCH-IPS"),
                           scene.creds, scene.token)
    assert response.status_code == 201
    assert db.scalar(select(Referral)).specialist_user_id == _user(db, "especialista.norte").id


def test_the_default_specialist_may_be_given_as_a_user_id(db, scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "interop_default_specialist", str(_user(db, "especialista.norte").id))
    assert signed_post(scene.http, SR, referral(*DOC), scene.creds, scene.token).status_code == 201


def test_no_specialist_at_all_is_202_and_the_admin_assigns_one(db, scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "interop_default_specialist", None)
    response = signed_post(scene.http, SR, referral(*DOC, organization_code="NO-SUCH-IPS"),
                           scene.creds, scene.token)
    assert response.status_code == 202 and db.scalar(select(func.count()).select_from(Referral)) == 0
    event = db.scalar(select(FhirInboundEvent))
    assert event.motivo_pendiente == "sin_especialista" and str(event.patient_id) == scene.patient["id"]

    admin = scene.client_as("admin")
    (item,) = admin.get("/api/v1/interop/pendientes").json()
    assert item["motivo"] == "sin_especialista"
    # Without naming a specialist there is still nobody to give it to.
    assert admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar", json={}).status_code == 409
    # A user who is not an especialista is refused as a target.
    not_a_specialist = admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar",
                                  json={"especialista_id": str(_user(db, "operador.norte").id)})
    assert not_a_specialist.status_code == 422
    specialist = _user(db, "especialista.norte")
    done = admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar",
                      json={"especialista_id": str(specialist.id)})
    assert done.status_code == 200, done.text
    db.expire_all()
    assert db.scalar(select(Referral)).specialist_user_id == specialist.id


def test_a_referral_for_an_unknown_patient_is_202_and_reconciles_to_a_referral(db, scene) -> None:
    code = _org_code(db, "especialista.norte")
    response = signed_post(scene.http, SR, referral("CC", "5550001112", organization_code=code),
                           scene.creds, scene.token)
    assert response.status_code == 202
    admin = scene.client_as("admin")
    (item,) = admin.get("/api/v1/interop/pendientes").json()
    assert item["recurso"] == "ServiceRequest"
    done = admin.post(f"/api/v1/interop/pendientes/{item['id']}/conciliar",
                      json={"paciente_id": scene.patient["id"]})
    assert done.status_code == 200, done.text
    db.expire_all()
    row = db.scalar(select(Referral))
    assert str(row.patient_id) == scene.patient["id"] and row.origen_externo == "equipo1"


def test_an_external_referral_does_not_break_the_fhir_sync(db, scene) -> None:
    from app.services import fhir_sync
    from tests.fhir_helpers import FakeFhir

    code = _org_code(db, "especialista.norte")
    signed_post(scene.http, SR, referral(*DOC, organization_code=code), scene.creds, scene.token)
    row = db.scalar(select(Referral))
    fake = FakeFhir()
    record = fhir_sync.sync_referral(db, fake, row, actor=_user(db, "admin"))
    assert record.fhir_resource_id
    (resource,) = fake.store["ServiceRequest"].values()
    assert resource["requester"]["display"] == "Sistema externo: equipo1"


# -- perimeter ----------------------------------------------------------------------------------


def test_the_rate_limit_groups_cover_the_partner_routes() -> None:
    assert ratelimit.classify("/oauth/token") == "oauth"
    assert ratelimit.classify("/interop/Observation") == "interop"
    assert ratelimit.classify("/interop/ServiceRequest") == "interop"
    assert ratelimit.classify("/api/v1/interop/pendientes") == "interop"


def test_the_partner_routes_are_published_at_the_root_and_the_admin_ones_under_api_v1() -> None:
    paths = {route.path for route in app.routes}
    assert {"/oauth/token", "/interop/Observation", "/interop/ServiceRequest"} <= paths
    assert {"/api/v1/interop/pendientes", "/api/v1/interop/clientes"} <= paths
