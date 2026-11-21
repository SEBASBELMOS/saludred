# ruff: noqa: F811
"""Any-partner mode: the professor may pair us with ANY team ("everyone should connect").

The docx (R31) asks only for "credenciales de sistema (client credentials o API key por
institucion)". Every strict extra of our receiver is a setting whose default keeps the strict
behaviour; these tests prove both sides of each switch:

* ``INTEROP_ALLOW_API_KEY``                   -> ``X-Api-Key: <client_id>:<client_secret>``
* ``INTEROP_REQUIRE_SIGNATURE``               -> unsigned messages accepted, signed ones verified
* ``INTEROP_ACCEPT_DOCUMENT_IDENTIFIER``      -> a Patient identified by document, not federated id
* ``INTEROP_SEND_DOCUMENT_IDENTIFIER``        -> our Bundle also carries the document
* the partner may answer with an update of the ServiceRequest instead of a Task
* ``<PREFIX>_API_KEY`` / ``<PREFIX>_SIGN``    -> outbound sending without token or signature
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.api.deps import get_current_user
from app.core import ratelimit
from app.core.config import get_settings
from app.main import app
from app.models import AuditLog, FhirInboundEvent, OutboundReferral, PartnerDiagnosticReport, Referral
from app.models.enums import AuditAction
from app.models.interop import InteropNonce
from app.services.interop import clientes, destinations, firma, roundtrip
from app.services.interop.cliente import InteropSender, SenderError, encode
from tests.interop_helpers import (
    federated,
    get_token,
    observation,
    outcome_issues,
    referral,
    register,
    signed_post,
)
from tests.reactive_helpers import audit_rows, inbox
from tests.test_ai_reports import CANARY, ml  # noqa: F401  (fixtures the scene needs)
from tests.test_bed_rules import client_as, db  # noqa: F401
from tests.test_interop_roundtrip import (
    ALL_SCOPES,
    DOC,
    SELF,
    _count,
    _interop_rows,
    _post,
    loop,  # noqa: F401  (the fixture builds the whole scene)
)

OBS = "/interop/Observation"
SR = "/interop/ServiceRequest"
BUNDLE = "/interop/Bundle"


def _setting(monkeypatch, **values) -> None:  # noqa: ANN001, ANN003
    for name, value in values.items():
        monkeypatch.setattr(get_settings(), name, value)


def _key(creds) -> str:  # noqa: ANN001
    return f"{creds.client_id}:{creds.client_secret}"


def _api_key_post(loop, path: str, resource: dict, *, creds=None, key: str | None = None,  # noqa: ANN001
                  sign: bool = True):
    """POST authenticated with ``X-Api-Key`` (no Bearer), signed like the sender signs."""

    creds = creds or loop.creds
    raw = encode(resource)
    headers = {"Content-Type": "application/fhir+json", "X-Api-Key": key if key is not None else _key(creds)}
    if sign:
        headers.update(firma.sign(creds.signing_secret, raw))
    return loop.http.post(path, content=raw, headers=headers)


def _obs(rid: str, value: float = 150) -> dict:
    return observation(*DOC, value=value, resource_id=rid)


# -- 1. API key authentication --------------------------------------------------------------------


def test_the_api_key_is_refused_by_default(loop) -> None:
    response = _api_key_post(loop, OBS, _obs("obs-key-off"))
    assert response.status_code == 401 and outcome_issues(response)
    assert "no esta habilitada" in outcome_issues(response)[0]["diagnostics"]
    assert response.headers["www-authenticate"] == "Bearer"
    (row,) = [r for r in audit_rows(loop.db, AuditAction.INTEROP_REJECTED) if r.metadata_json["recurso"] == "Observation"]
    assert row.metadata_json["motivo"] == "api_key_desactivada" and row.metadata_json["origen"] == "externo"
    assert _count(loop.db, FhirInboundEvent, FhirInboundEvent.fhir_resource_type == "Observation") == 0


def test_the_api_key_authenticates_a_partner_and_leaves_the_same_external_audit(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    response = _api_key_post(loop, OBS, _obs("obs-key-on"))
    assert response.status_code == 201, response.text
    (received,) = _interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "Observation")
    assert received.metadata_json["origen"] == "externo" and received.metadata_json["cliente"] == SELF
    assert received.metadata_json["codigo_http"] == 201
    # The same entry works for the other resources of the exchange (scope of each route).
    bundle = _bundle_for_known_patient()
    assert _api_key_post(loop, BUNDLE, bundle).status_code == 201


def test_the_api_key_stamps_last_used_at_at_most_once_per_minute(loop, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.models import InteropClient

    _setting(monkeypatch, interop_allow_api_key=True)
    row = loop.db.scalar(select(InteropClient).where(InteropClient.client_id == SELF))
    row.last_used_at = None
    loop.db.commit()
    assert _api_key_post(loop, OBS, _obs("obs-used-1")).status_code == 201
    loop.db.expire_all()
    first = loop.db.scalar(select(InteropClient).where(InteropClient.client_id == SELF)).last_used_at
    assert first is not None
    assert _api_key_post(loop, OBS, _obs("obs-used-2")).status_code == 201
    loop.db.expire_all()
    assert loop.db.scalar(select(InteropClient).where(InteropClient.client_id == SELF)).last_used_at == first
    # A stamp older than a minute is refreshed.
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    loop.db.scalar(select(InteropClient).where(InteropClient.client_id == SELF)).last_used_at = stale
    loop.db.commit()
    assert _api_key_post(loop, OBS, _obs("obs-used-3")).status_code == 201
    loop.db.expire_all()
    renewed = loop.db.scalar(select(InteropClient).where(InteropClient.client_id == SELF)).last_used_at
    if renewed.tzinfo is None:
        renewed = renewed.replace(tzinfo=timezone.utc)
    assert renewed > stale + timedelta(minutes=4)


def _bundle_for_known_patient() -> dict:
    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    order = roundtrip.build_referral_request(f"sr-{uuid.uuid4().hex}", "Control", "Cardiologia", "rutina")
    order["subject"] = {"identifier": fid}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": {"resourceType": "Patient", "identifier": [fid]}}, {"resource": order}]}


def test_a_bearer_token_wins_when_both_credentials_are_sent(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    token = get_token(loop.http, loop.creds)
    response = signed_post(loop.http, OBS, _obs("obs-both"), loop.creds, token)
    assert response.status_code == 201
    raw = encode(_obs("obs-both-2"))
    headers = {"Content-Type": "application/fhir+json", "Authorization": f"Bearer {token}",
               "X-Api-Key": "nobody:garbage", **firma.sign(loop.creds.signing_secret, raw)}
    assert loop.http.post(OBS, content=raw, headers=headers).status_code == 201


@pytest.mark.parametrize(
    "key",
    ["", "no-separator", "equipo:", ":secret", "ghost:whatever", "saludred-self:wrong-secret", f"{SELF.upper()}x:y"],
)
def test_a_bad_api_key_is_a_401_that_reveals_nothing(loop, monkeypatch, key) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    response = _api_key_post(loop, OBS, _obs("obs-bad-key"), key=key)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert _count(loop.db, FhirInboundEvent, FhirInboundEvent.fhir_resource_type == "Observation") == 0
    if key:  # unknown client and wrong secret answer the same body
        other = _api_key_post(loop, OBS, _obs("obs-bad-key-2"), key="ghost:whatever")
        assert response.json() == other.json()


def test_a_disabled_client_cannot_use_its_api_key(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    client = clientes.get_active_client(loop.db, SELF)
    client.is_active = False
    loop.db.commit()
    assert _api_key_post(loop, OBS, _obs("obs-inactive")).status_code == 401


def test_the_api_key_keeps_the_scope_of_each_route(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    only_obs = register(loop.db, "solo-observaciones", (clientes.SCOPE_OBSERVATION,))
    refused = _api_key_post(loop, BUNDLE, _bundle_for_known_patient(), creds=only_obs)
    assert refused.status_code == 403
    assert "system/Bundle.write" in outcome_issues(refused)[0]["diagnostics"]
    allowed = _api_key_post(loop, OBS, _obs("obs-scope"), creds=only_obs)
    assert allowed.status_code == 201, allowed.text
    (row,) = [r for r in audit_rows(loop.db, AuditAction.INTEROP_REJECTED) if r.metadata_json["recurso"] == "Bundle"]
    assert row.metadata_json["motivo"] == "sin_alcance" and row.metadata_json["cliente"] == "solo-observaciones"


def test_the_api_key_keeps_the_size_limit_and_the_signature(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    huge = _obs("obs-huge")
    huge["note"] = [{"text": "x" * 300_000}]
    assert _api_key_post(loop, OBS, huge).status_code == 413
    # Required signature by default, also with an API key.
    assert _api_key_post(loop, OBS, _obs("obs-unsigned"), sign=False).status_code == 401
    wrong = firma.sign("not-the-secret", encode(_obs("obs-wrong")))
    raw = encode(_obs("obs-wrong"))
    response = loop.http.post(OBS, content=raw, headers={
        "Content-Type": "application/fhir+json", "X-Api-Key": _key(loop.creds), **wrong})
    assert response.status_code == 401


def test_api_key_requests_share_the_interop_rate_limit(loop, monkeypatch) -> None:
    _setting(monkeypatch, rate_limit_enabled=True, rate_limit_max_requests=2, interop_allow_api_key=True)
    ratelimit.reset()
    try:
        codes = [_api_key_post(loop, OBS, _obs(f"obs-rl-{n}"), key="ghost:x").status_code for n in range(3)]
        assert codes == [401, 401, 429]
    finally:
        ratelimit.reset()


def test_two_api_key_clients_behind_one_address_get_separate_buckets(loop, monkeypatch) -> None:
    _setting(monkeypatch, rate_limit_enabled=True, rate_limit_max_requests=2, interop_allow_api_key=True)
    ratelimit.reset()
    try:
        first = [_api_key_post(loop, OBS, _obs(f"obs-rl-a{n}"), key="ghost-a:x").status_code for n in range(3)]
        assert first == [401, 401, 429]  # the first client exhausted ITS bucket
        # The second client, same address (the tunnel), is not throttled by the first one.
        second = [_api_key_post(loop, OBS, _obs(f"obs-rl-b{n}"), key="Ghost-B:x").status_code for n in range(2)]
        assert second == [401, 401]
        # The identity is normalised: the same id in another case shares the bucket.
        assert _api_key_post(loop, OBS, _obs("obs-rl-b2"), key="GHOST-b:y").status_code == 429
    finally:
        ratelimit.reset()


@pytest.mark.parametrize("path", ["/api/v1/patients", "/api/v1/referrals", "/api/v1/interop/clientes",
                                  "/api/v1/interop/remisiones", "/api/v1/auth/me"])
def test_an_api_key_never_works_on_the_user_api(loop, monkeypatch, path) -> None:
    _setting(monkeypatch, interop_allow_api_key=True)
    app.dependency_overrides.pop(get_current_user, None)  # no staff user is impersonated
    response = loop.http.get(path, headers={"X-Api-Key": _key(loop.creds)})
    assert response.status_code in (401, 403), (path, response.status_code)
    response = loop.http.get(path, headers={"X-Api-Key": _key(loop.creds),
                                            "Authorization": "Bearer not-a-user-token"})
    assert response.status_code in (401, 403), (path, response.status_code)


# -- 2. Signature optional per setting -------------------------------------------------------------


def test_the_signature_is_required_by_default(loop) -> None:
    assert get_settings().interop_require_signature is True
    token = get_token(loop.http, loop.creds)
    response = loop.http.post(OBS, content=encode(_obs("obs-nosig")), headers={
        "Content-Type": "application/fhir+json", "Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "firma" in outcome_issues(response)[0]["diagnostics"].lower()


def test_without_the_requirement_an_unsigned_message_is_accepted_and_nonce_and_date_are_skipped(
    loop, monkeypatch
) -> None:
    _setting(monkeypatch, interop_require_signature=False)
    token = get_token(loop.http, loop.creds)
    headers = {"Content-Type": "application/fhir+json", "Authorization": f"Bearer {token}"}
    for n in range(2):
        response = loop.http.post(OBS, content=encode(_obs(f"obs-unsigned-{n}")), headers=headers)
        assert response.status_code == 201, response.text
    assert _count(loop.db, InteropNonce) == 0  # no nonce was registered, none was needed


def test_an_unsigned_message_works_with_an_api_key_when_both_switches_are_on(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_require_signature=False, interop_allow_api_key=True)
    response = _api_key_post(loop, OBS, _obs("obs-key-unsigned"), sign=False)
    assert response.status_code == 201, response.text


def test_a_signature_that_is_present_is_still_verified_when_the_requirement_is_off(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_require_signature=False)
    token = get_token(loop.http, loop.creds)
    good = signed_post(loop.http, OBS, _obs("obs-sig-good"), loop.creds, token)
    assert good.status_code == 201
    wrong_secret = signed_post(loop.http, OBS, _obs("obs-sig-bad"), loop.creds, token, sign_with="not-the-secret")
    assert wrong_secret.status_code == 401
    stale = signed_post(loop.http, OBS, _obs("obs-sig-old"), loop.creds, token, now=time.time() - 10_000)
    assert stale.status_code == 401
    tampered = signed_post(loop.http, OBS, _obs("obs-sig-tamper"), loop.creds, token,
                           send_body=encode(_obs("obs-sig-other")))
    assert tampered.status_code == 401
    replay = signed_post(loop.http, OBS, _obs("obs-sig-nonce"), loop.creds, token, nonce="nonce-used-twice-1")
    again = signed_post(loop.http, OBS, _obs("obs-sig-nonce-2"), loop.creds, token, nonce="nonce-used-twice-1")
    assert replay.status_code == 201 and again.status_code == 409  # a nonce that is sent is still single use


# -- 3. Patient matching without a federated id ----------------------------------------------------


def _document(kind: str = "CC", number: str = DOC[1]) -> dict:
    return {"system": get_settings().interop_document_system, "type": {"text": kind}, "value": number}


def _obs_with_document(rid: str, kind: str = "CC", number: str = DOC[1]) -> dict:
    resource = _obs(rid)
    resource["subject"] = {"identifier": _document(kind, number)}
    return resource


def test_a_document_identifier_is_refused_by_default(loop) -> None:
    assert get_settings().interop_accept_document_identifier is False
    response = _post(loop, OBS, _obs_with_document("obs-doc-off"))
    assert response.status_code == 422 and outcome_issues(response)
    assert _count(loop.db, FhirInboundEvent, FhirInboundEvent.fhir_resource_type == "Observation") == 0


def test_with_the_setting_the_document_resolves_the_patient_and_is_not_stored(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, OBS, _obs_with_document("obs-doc-on"))
    assert response.status_code == 201, response.text
    loop.db.expire_all()
    (event,) = loop.db.scalars(select(FhirInboundEvent).where(
        FhirInboundEvent.fhir_resource_type == "Observation")).all()
    assert str(event.patient_id) == loop.patient["id"]
    assert event.id_federado_recibido == federated(*DOC)
    stored = json.dumps(event.resource)
    assert DOC[1] not in stored and federated(*DOC) in stored  # the number was only a way to find the patient
    # The same patient through the federated id still works, and lands on the same person.
    assert _post(loop, OBS, _obs("obs-doc-fed")).status_code == 201


def test_a_document_of_an_unknown_patient_waits_in_the_queue_with_a_202(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, OBS, _obs_with_document("obs-doc-unknown", number="5550001112"))
    assert response.status_code == 202, response.text
    loop.db.expire_all()
    (event,) = loop.db.scalars(select(FhirInboundEvent).where(
        FhirInboundEvent.fhir_resource_type == "Observation")).all()
    assert event.id_federado_recibido == federated("CC", "5550001112")
    assert "5550001112" not in json.dumps(event.resource)


@pytest.mark.parametrize(
    "identifier",
    [
        {"system": "urn:x:documento", "type": {"text": "RC"}, "value": DOC[1]},       # no federated id for RC
        {"system": "urn:x:documento", "value": DOC[1]},                               # no type at all
        {"system": "urn:x:documento", "type": {"text": "CC"}, "value": "..."},        # nothing left to hash
        {"system": "urn:x:documento", "type": {"text": "CC"}},                        # no number
    ],
)
def test_an_unusable_document_is_still_a_422(loop, monkeypatch, identifier) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    resource = _obs("obs-doc-bad")
    resource["subject"] = {"identifier": identifier}
    assert _post(loop, OBS, resource).status_code == 422


def test_the_document_type_can_travel_as_a_coding_or_in_the_system(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    coded = _obs("obs-doc-coding")
    coded["subject"] = {"identifier": {"type": {"coding": [{"code": "CC"}]}, "value": "7.788.990.011"}}
    assert _post(loop, OBS, coded).status_code == 201
    by_system = _obs("obs-doc-system")
    by_system["subject"] = {"identifier": {"system": "https://registro.example/CC", "value": DOC[1]}}
    assert _post(loop, OBS, by_system).status_code == 201


def _document_bundle(*, patient_extra: dict | None = None, number: str = DOC[1]) -> dict:
    identifier = _document(number=number)
    order = roundtrip.build_referral_request(f"sr-{uuid.uuid4().hex}", "Control", "Cardiologia", "rutina")
    order["subject"] = {"identifier": identifier}
    patient = {"resourceType": "Patient", "identifier": [identifier], **(patient_extra or {})}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": patient}, {"resource": order}]}


def test_a_bundle_identified_by_document_is_accepted_only_with_the_setting(loop, monkeypatch) -> None:
    assert _post(loop, BUNDLE, _document_bundle()).status_code == 422
    assert loop.received() is None
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, BUNDLE, _document_bundle())
    assert response.status_code == 201, response.text
    assert loop.received() is not None
    loop.db.expire_all()
    (event,) = loop.db.scalars(select(FhirInboundEvent).where(FhirInboundEvent.fhir_resource_type == "Bundle")).all()
    assert DOC[1] not in json.dumps(event.resource)
    assert event.id_federado_recibido == federated(*DOC)


def _number_anywhere(db, number: str) -> list[str]:  # noqa: ANN001
    """Where the raw document number is stored: inbound events, partner reports and the audit."""

    db.expire_all()
    found: list[str] = []
    for event in db.scalars(select(FhirInboundEvent)):
        if number in json.dumps(event.resource):
            found.append("fhir_inbound_events")
    for report in db.scalars(select(PartnerDiagnosticReport)):
        if number in json.dumps(report.resource):
            found.append("partner_diagnostic_reports")
    for row in db.scalars(select(AuditLog)):
        if number in json.dumps(row.metadata_json or {}, default=str):
            found.append("audit_log")
    return found


def _bundle_with_report(*, federated_patient: bool, report_subject: dict) -> dict:
    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    document = _document()
    order = roundtrip.build_referral_request(f"sr-{uuid.uuid4().hex}", "Control", "Cardiologia", "rutina")
    order["subject"] = {"identifier": fid if federated_patient else document}
    report = {"resourceType": "DiagnosticReport", "status": "final", "subject": {"identifier": report_subject},
              "code": {"text": "Informe"}, "conclusion": "Sin hallazgos"}
    patient = {"resourceType": "Patient", "identifier": [fid, document] if federated_patient else [document]}
    return {"resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": patient}, {"resource": order}, {"resource": report}]}


def test_the_document_of_a_diagnostic_report_subject_is_never_stored(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, BUNDLE, _bundle_with_report(federated_patient=False, report_subject=_document()))
    assert response.status_code == 201, response.text
    loop.db.expire_all()
    (report,) = loop.db.scalars(select(PartnerDiagnosticReport)).all()
    assert report.resource["subject"]["identifier"]["value"] == federated(*DOC)
    assert _number_anywhere(loop.db, DOC[1]) == []


def test_strict_mode_drops_the_document_that_travels_next_to_the_federated_id(loop, monkeypatch) -> None:
    assert get_settings().interop_accept_document_identifier is False
    bundle = _bundle_with_report(federated_patient=True,
                                 report_subject={"system": get_settings().federated_system,
                                                 "value": federated(*DOC)})
    response = _post(loop, BUNDLE, bundle)
    assert response.status_code == 201, response.text  # the federated id is enough
    assert _number_anywhere(loop.db, DOC[1]) == []
    loop.db.expire_all()
    (event,) = loop.db.scalars(select(FhirInboundEvent).where(FhirInboundEvent.fhir_resource_type == "Bundle")).all()
    patient = next(e["resource"] for e in event.resource["entry"] if e["resource"]["resourceType"] == "Patient")
    assert [i["system"] for i in patient["identifier"]] == [get_settings().federated_system]
    # And the raw document never reaches the stored partner report either (reconcile path included).
    assert _number_anywhere(loop.db, DOC[1]) == []


def test_canonical_resource_is_independent_of_the_setting() -> None:
    from app.services.interop import perfil

    fid = {"system": get_settings().federated_system, "value": federated(*DOC)}
    patient = {"resourceType": "Patient", "identifier": [_document(), fid]}
    out = perfil.canonical_resource({"resourceType": "Bundle", "entry": [{"resource": patient}]})
    assert out["entry"][0]["resource"]["identifier"] == [fid]
    assert DOC[1] in json.dumps(patient)  # the received object is not mutated


@pytest.mark.parametrize(
    "extra",
    [
        {"telecom": [{"system": "phone", "value": CANARY["phone"]}]},
        {"address": [{"text": CANARY["address"]}]},
        {"name": [{"family": CANARY["last_name"]}]},
        {"birthDate": "1961-03-09"},
    ],
)
def test_direct_identifiers_stay_rejected_even_with_the_document_identifier(loop, monkeypatch, extra) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, BUNDLE, _document_bundle(patient_extra=extra))
    assert response.status_code == 422, response.text
    assert loop.received() is None


def test_a_queued_document_bundle_can_be_reconciled(loop, monkeypatch) -> None:
    _setting(monkeypatch, interop_accept_document_identifier=True)
    response = _post(loop, BUNDLE, _document_bundle(number="5550001112"))
    assert response.status_code == 202, response.text
    (queued,) = loop.db.scalars(select(FhirInboundEvent).where(FhirInboundEvent.fhir_resource_type == "Bundle")).all()
    assert "5550001112" not in json.dumps(queued.resource)
    # Reconciling re-validates the stored copy: it carries the federated id, so it passes in any mode.
    _setting(monkeypatch, interop_accept_document_identifier=False)
    done = loop.admin.post(f"/api/v1/interop/pendientes/{queued.id}/conciliar", json={"paciente_id": loop.patient["id"]})
    assert done.status_code == 200, done.text


def test_our_bundle_is_minimal_by_default_and_adds_the_document_only_when_asked(loop, monkeypatch) -> None:
    loop.grant_consent()
    loop.sent_referral()
    (minimal,) = loop.wire.of("Bundle")
    text = json.dumps(minimal)
    assert DOC[1] not in text
    patient = next(e["resource"] for e in minimal["entry"] if e["resource"]["resourceType"] == "Patient")
    assert len(patient["identifier"]) == 1

    _setting(monkeypatch, interop_send_document_identifier=True)
    loop.wire.sent.clear()
    assert loop.refer().status_code == 201
    (negotiated,) = loop.wire.of("Bundle")
    patient = next(e["resource"] for e in negotiated["entry"] if e["resource"]["resourceType"] == "Patient")
    assert set(patient) == {"resourceType", "identifier"}  # still no name, phone, address or birth date
    system = get_settings().federated_system
    by_system = {i["system"]: i for i in patient["identifier"]}
    assert by_system[system]["value"] == federated(*DOC)  # the federated id is always sent
    document = by_system[get_settings().interop_document_system]
    assert document["value"] == DOC[1] and document["type"] == {"text": "CC"}
    # Every other resource keeps the federated subject alone.
    for entry in negotiated["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] != "Patient":
            assert resource["subject"]["identifier"]["system"] == system
    # Our own strict receiver still accepts it: it simply ignores the extra identifier, and
    # the stored copy of the Bundle does NOT keep the number either.
    assert loop.received() is not None
    loop.db.expire_all()
    for event in loop.db.scalars(select(FhirInboundEvent).where(FhirInboundEvent.fhir_resource_type == "Bundle")):
        assert DOC[1] not in json.dumps(event.resource)
        assert federated(*DOC) in json.dumps(event.resource)


# -- 4. The partner answers with an update of the ServiceRequest ------------------------------------


def _reply(sent: dict, status: str, **extra) -> dict:  # noqa: ANN003
    return {"resourceType": "ServiceRequest", "id": sent["solicitud_id"], "status": status, "intent": "order",
            "identifier": [{"system": roundtrip.REQUEST_SYSTEM, "value": sent["solicitud_id"]}], **extra}


def test_an_active_service_request_update_accepts_the_outbound_referral(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    response = _post(loop, SR, _reply(sent, "active"))
    assert response.status_code == 200, response.text
    assert loop.outbound()[0]["estado"] == "aceptada"
    (note,) = inbox(loop.db, "operador.norte", "respuesta_socio")
    assert note.data["estado"] == "aceptada" and note.data["cliente"] == SELF
    (row,) = _interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "ServiceRequest")
    assert row.metadata_json["origen"] == "externo" and row.metadata_json["resultado"] == "aceptada"
    stored = loop.db.get(OutboundReferral, uuid.UUID(sent["id"]))
    assert stored.response_task["resourceType"] == "ServiceRequest"
    # It was an answer, not a new referral: nothing new reached the inbox of B.
    assert _count(loop.db, Referral, Referral.origen_externo.is_not(None)) == 1


@pytest.mark.parametrize(
    "reason",
    [{"note": [{"text": "Sin cupos este mes"}]}, {"statusReason": {"text": "Sin cupos este mes"}},
     {"reasonCode": [{"text": "Sin cupos este mes"}]}],
    ids=["note", "statusReason", "reasonCode"],
)
def test_a_revoked_service_request_update_rejects_it_with_the_reason(loop, reason) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    response = _post(loop, SR, _reply(sent, "revoked", **reason))
    assert response.status_code == 200, response.text
    (row,) = loop.outbound()
    assert row["estado"] == "rechazada" and row["resumen_respuesta"] == "Sin cupos este mes"
    (note,) = inbox(loop.db, "operador.norte", "respuesta_socio")
    assert "Sin cupos este mes" in note.message


def test_a_reply_without_a_usable_status_or_reason_is_a_422_and_changes_nothing(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    for body in (_reply(sent, "revoked"), _reply(sent, "completed"), _reply(sent, "on-hold", note=[{"text": "x"}])):
        response = _post(loop, SR, body)
        assert response.status_code == 422 and outcome_issues(response), body["status"]
    assert loop.outbound()[0]["estado"] == "enviada"
    assert inbox(loop.db, "operador.norte", "respuesta_socio") == []


def test_a_replayed_reply_is_idempotent_and_a_contradiction_is_a_conflict(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    assert _post(loop, SR, _reply(sent, "active")).status_code == 200
    assert _post(loop, SR, _reply(sent, "active")).status_code == 200  # a replay
    assert len(inbox(loop.db, "operador.norte", "respuesta_socio")) == 1
    assert len(_interop_rows(loop.db, AuditAction.INTEROP_RECEIVED, "ServiceRequest")) == 1
    contradiction = _post(loop, SR, _reply(sent, "revoked", note=[{"text": "Cambio de opinion"}]))
    assert contradiction.status_code == 409
    assert loop.outbound()[0]["estado"] == "aceptada"
    # A Task for the same referral is the same answer: it is recognised as already processed.
    task = {"resourceType": "Task", "status": "accepted", "intent": "order",
            "focus": {"identifier": {"system": roundtrip.REQUEST_SYSTEM, "value": sent["solicitud_id"]}}}
    assert _post(loop, "/interop/Task", task).status_code == 200


def test_another_partner_cannot_answer_with_a_service_request_either(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    intruder = register(loop.db, "otro-socio", ALL_SCOPES)
    response = signed_post(loop.http, SR, _reply(sent, "active"), intruder, get_token(loop.http, intruder))
    # Not a reply for them (it is not their referral), so it is judged as a new referral: no subject, 422.
    assert response.status_code == 422
    assert loop.outbound()[0]["estado"] == "enviada"
    assert inbox(loop.db, "operador.norte", "respuesta_socio") == []


def test_a_new_referral_is_not_mistaken_for_a_reply(loop) -> None:
    loop.grant_consent()
    sent = loop.sent_referral()
    # A brand-new referral from the partner, even one that uses our identifier system with a value
    # that matches nothing of ours, still creates a referral.
    fresh = referral(*DOC, resource_id="partner-ref-1")
    fresh["identifier"] = [{"system": roundtrip.REQUEST_SYSTEM, "value": "sr-not-ours"}]
    response = _post(loop, SR, fresh)
    assert response.status_code == 201, response.text
    assert _count(loop.db, Referral, Referral.origen_externo.is_not(None)) == 2  # the Bundle's and this one
    assert loop.outbound()[0]["estado"] == "enviada"  # untouched
    assert sent["estado"] == "enviada"


# -- 5. Outbound sending with an API key ------------------------------------------------------------


def _partner(db, monkeypatch, prefix: str, **env):  # noqa: ANN001, ANN003
    clientes.register_client(
        db, client_id=prefix.lower().replace("_", "-"), name=f"Socio {prefix}",
        scopes=[clientes.SCOPE_BUNDLE, clientes.SCOPE_TASK], allow_plaintext=True, outbound_env_prefix=prefix)
    for name, value in env.items():
        monkeypatch.setenv(f"{prefix}_{name}", value)
    return prefix.lower().replace("_", "-")


def _with_transport(sender: InteropSender, status: int = 201) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"resourceType": "OperationOutcome", "issue": []})

    sender._http.close()  # noqa: SLF001 -- the real client built by the constructor
    sender._http = httpx.Client(transport=httpx.MockTransport(handler))  # noqa: SLF001
    return calls


def test_an_api_key_destination_sends_x_api_key_without_asking_for_a_token(db, monkeypatch) -> None:
    client_id = _partner(db, monkeypatch, "SOCIOKEY", BASE_URL="https://partner.example",
                         API_KEY="their-id:their-secret", SIGNING_SECRET="signing-secret-shared")
    sender = destinations.sender_for_partner(db, client_id)
    calls = _with_transport(sender)
    result = sender.enviar_bundle({"resourceType": "Bundle", "type": "collection", "entry": []})
    assert result.entregado and result.status == 201
    assert [c.url.path for c in calls] == ["/interop/Bundle"]  # no /oauth/token round trip
    request = calls[0]
    assert request.headers["x-api-key"] == "their-id:their-secret"
    assert "authorization" not in request.headers
    assert {"x-interop-fecha", "x-interop-nonce", "x-interop-firma"} <= set(request.headers)  # signed by default
    sender.close()


def test_sign_false_skips_the_signature_and_the_signing_secret(db, monkeypatch) -> None:
    client_id = _partner(db, monkeypatch, "SOCIONOSIGN", BASE_URL="https://partner.example",
                         API_KEY="their-key", SIGN="false")
    sender = destinations.sender_for_partner(db, client_id)  # no SIGNING_SECRET and no client id/secret
    calls = _with_transport(sender, status=200)
    assert sender.enviar_task({"resourceType": "Task", "status": "accepted"}).entregado
    (request,) = calls
    assert request.url.path == "/interop/Task" and request.headers["x-api-key"] == "their-key"
    assert not any(name.startswith("x-interop-") for name in request.headers)
    assert "authorization" not in request.headers
    sender.close()


@pytest.mark.parametrize("value", ["False", "0", "no", "off"])
def test_every_false_spelling_turns_the_signature_off(db, monkeypatch, value) -> None:
    client_id = _partner(db, monkeypatch, "SOCIOFALSE", BASE_URL="https://partner.example",
                         API_KEY="k", SIGN=value)
    sender = destinations.sender_for_partner(db, client_id)
    assert sender._sign is False  # noqa: SLF001
    sender.close()


def test_the_destination_without_an_api_key_keeps_the_token_and_the_signature(db, monkeypatch) -> None:
    client_id = _partner(db, monkeypatch, "SOCIOSTRICT", BASE_URL="https://partner.example",
                         CLIENT_ID="id", CLIENT_SECRET="secret", SIGNING_SECRET="signing")
    sender = destinations.sender_for_partner(db, client_id)
    assert sender._api_key is None and sender._sign is True  # noqa: SLF001
    sender.close()


@pytest.mark.parametrize(
    "env",
    [
        {"BASE_URL": "https://p.example", "API_KEY": "k"},                       # signature on, no signing secret
        {"API_KEY": "k", "SIGN": "false"},                                        # no base url
        {"BASE_URL": "https://p.example", "CLIENT_ID": "id", "SIGN": "false"},   # no api key and no client secret
    ],
)
def test_an_incomplete_destination_is_still_refused(db, monkeypatch, env) -> None:
    client_id = _partner(db, monkeypatch, "SOCIOHALF", **env)
    with pytest.raises(SenderError, match="incompleta"):
        destinations.sender_for_partner(db, client_id)


def test_a_401_with_an_api_key_is_final_and_does_not_ask_for_a_token(db, monkeypatch) -> None:
    client_id = _partner(db, monkeypatch, "SOCIO401", BASE_URL="https://partner.example", API_KEY="bad",
                         SIGN="false")
    sender = destinations.sender_for_partner(db, client_id)
    calls = _with_transport(sender, status=401)
    result = sender.enviar_bundle({"resourceType": "Bundle"})
    assert not result.entregado and result.status == 401 and "API key" in result.mensaje
    assert [c.url.path for c in calls] == ["/interop/Bundle"]
    sender.close()
