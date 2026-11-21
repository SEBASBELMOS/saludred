# ruff: noqa: F811
"""SaludRed as the sender (slides 28-30): token, signature and how each code is handled.

The partner is faked with ``httpx.MockTransport`` (no network); one test also drives the
real receiver of this application through a ``TestClient``.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models import AuditLog, Observation
from app.models.enums import AuditAction, ObservationOrigin
from app.services.interop import cliente, firma
from app.services.interop.cliente import InteropSender, SenderError
from tests.interop_helpers import FEDERATED_SECRET, federated, register
from tests.test_ai_reports import CANARY, _create_patient  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401

SIGNING = "signing-secret-of-the-test-partner"
SYSTEM = "urn:uao:salud-digital:paciente-federado"


class FakePartner:
    """A scripted partner: ``statuses`` are the answers to successive interop POSTs."""

    def __init__(self, statuses: list[int], *, outcome: dict | None = None, retry_after: str | None = None):
        self.statuses = list(statuses)
        self.outcome = outcome or {"resourceType": "OperationOutcome", "issue": []}
        self.retry_after = retry_after
        self.token_calls = 0
        self.interop_calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            self.token_calls += 1
            return httpx.Response(200, json={
                "access_token": f"token-{self.token_calls}", "token_type": "Bearer",
                "expires_in": 600, "scope": "system/Observation.write",
            })
        self.interop_calls.append(request)
        status = self.statuses.pop(0) if self.statuses else 201
        headers = {"retry-after": self.retry_after} if self.retry_after else {}
        return httpx.Response(status, json=self.outcome, headers=headers)


def sender_for(partner: FakePartner, sleeps: list[float] | None = None, **kw) -> InteropSender:  # noqa: ANN003
    http = httpx.Client(transport=httpx.MockTransport(partner))
    return InteropSender(
        "https://partner.test", "equipo1", "client-secret", SIGNING, http=http,
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)), **kw,
    )


def message() -> dict:
    return cliente.build_observation("2339-0", 182, "CC", "1144123456", system=SYSTEM, secret=FEDERATED_SECRET)


def issue(text: str) -> dict:
    return {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": "value", "diagnostics": text}]}


# -- builders -----------------------------------------------------------------------------


def test_the_observation_is_a_conforming_profile_message_with_a_federated_subject() -> None:
    from app.services.interop import perfil

    resource = message()
    assert resource["subject"]["identifier"]["value"] == federated("CC", "1144123456")
    assert "1144123456" not in json.dumps(resource)  # the document never travels
    assert resource["valueQuantity"] == {"value": 182, "unit": "mg/dL", "system": perfil.UCUM, "code": "mg/dL"}
    assert get_settings().federated_system == SYSTEM  # the profile compares with the setting
    assert perfil.validate_observation(resource) == []


def test_the_referral_is_a_conforming_service_request() -> None:
    from app.services.interop import perfil

    resource = cliente.build_service_request(
        "Control", "CC", "1144123456", system=SYSTEM, secret=FEDERATED_SECRET,
        organization_code="IPS-NORTE", reason_snomed="38341003")
    assert perfil.validate_service_request(resource) == []
    assert resource["code"]["coding"][0]["code"] == "3457005"


def test_without_the_federated_secret_the_sender_refuses_to_build_anything(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "federated_secret", None)
    with pytest.raises(SenderError, match="FEDERATED_SECRET"):
        cliente.build_observation("2339-0", 182, "CC", "1", system=SYSTEM)


def test_the_encoded_body_is_what_gets_signed() -> None:
    partner = FakePartner([201])
    sender = sender_for(partner)
    resource = message()
    assert sender.enviar_observacion(resource).status == 201
    (request,) = partner.interop_calls
    assert request.content == cliente.encode(resource)
    signed = firma.parse_headers(
        request.headers[firma.HEADER_DATE], request.headers[firma.HEADER_NONCE],
        request.headers[firma.HEADER_SIGNATURE])
    firma.check(SIGNING, signed, request.content)  # does not raise
    assert request.headers["authorization"] == "Bearer token-1"


def test_bundle_transport_signs_and_posts_the_exact_collection():
    partner = FakePartner([201])
    sender = sender_for(partner)
    bundle = {"resourceType": "Bundle", "type": "collection", "entry": []}
    result = sender.enviar_bundle(bundle)
    assert result.entregado and result.recurso == "Bundle"
    (request,) = partner.interop_calls
    assert request.url.path == "/interop/Bundle"
    assert request.content == cliente.encode(bundle)
    signed = firma.parse_headers(
        request.headers[firma.HEADER_DATE], request.headers[firma.HEADER_NONCE],
        request.headers[firma.HEADER_SIGNATURE],
    )
    firma.check(SIGNING, signed, request.content)


# -- each code ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [201, 202])
def test_201_and_202_are_delivered(status: int) -> None:
    result = sender_for(FakePartner([status])).enviar_observacion(message())
    assert (result.status, result.entregado, result.intentos) == (status, True, 1)


def test_a_401_asks_for_a_new_token_once_and_sends_again() -> None:
    partner = FakePartner([401, 201])
    result = sender_for(partner).enviar_observacion(message())
    assert (result.status, result.entregado, result.intentos) == (201, True, 2)
    assert partner.token_calls == 2
    first, second = partner.interop_calls
    assert first.headers["authorization"] == "Bearer token-1"
    assert second.headers["authorization"] == "Bearer token-2"
    # Each attempt is signed afresh: a nonce is single use.
    assert first.headers[firma.HEADER_NONCE] != second.headers[firma.HEADER_NONCE]


def test_a_second_401_is_reported_not_retried_forever() -> None:
    partner = FakePartner([401, 401])
    result = sender_for(partner).enviar_observacion(message())
    assert (result.status, result.entregado) == (401, False)
    assert "secreto de firma" in result.mensaje and partner.token_calls == 2


def test_a_403_reports_the_missing_scope_and_does_not_retry() -> None:
    partner = FakePartner([403], outcome=issue("El token no incluye el alcance system/ServiceRequest.write"))
    result = sender_for(partner).enviar_remision(
        cliente.build_service_request("m", "CC", "1", system=SYSTEM, secret=FEDERATED_SECRET))
    assert (result.status, result.entregado, result.intentos) == (403, False, 1)
    assert "system/ServiceRequest.write" in result.mensaje and "system/Observation.write" in result.mensaje
    assert len(partner.interop_calls) == 1


def test_a_409_means_already_delivered() -> None:
    result = sender_for(FakePartner([409], outcome=issue("Mensaje repetido"))).enviar_observacion(message())
    assert (result.status, result.entregado) == (409, True) and "Ya entregado" in result.mensaje


def test_a_422_shows_the_operation_outcome() -> None:
    result = sender_for(FakePartner([422], outcome=issue("Unidad invalida para 2339-0"))).enviar_observacion(message())
    assert (result.status, result.entregado) == (422, False)
    assert "Unidad invalida" in result.mensaje and result.issues[0]["code"] == "value"


def test_a_5xx_is_retried_with_a_growing_wait() -> None:
    sleeps: list[float] = []
    partner = FakePartner([503, 500, 201])
    result = sender_for(partner, sleeps).enviar_observacion(message())
    assert (result.status, result.entregado, result.intentos) == (201, True, 3)
    assert sleeps == [2.0, 4.0]


def test_a_5xx_that_never_recovers_gives_up() -> None:
    result = sender_for(FakePartner([500, 500, 500])).enviar_observacion(message())
    assert (result.status, result.entregado, result.intentos) == (500, False, 3)


def test_a_429_waits_retry_after() -> None:
    sleeps: list[float] = []
    result = sender_for(FakePartner([429, 201], retry_after="7"), sleeps).enviar_observacion(message())
    assert result.entregado and sleeps == [7.0]


def test_an_unreachable_partner_is_a_sender_error_at_the_token_step() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sender = InteropSender("https://partner.test", "c", "s", SIGNING,
                           http=httpx.Client(transport=httpx.MockTransport(down)), sleep=lambda _s: None)
    with pytest.raises(SenderError, match="Sin respuesta del socio"):
        sender.enviar_observacion(message())


def test_an_unreachable_partner_after_the_token_is_retried_then_reported() -> None:
    sleeps: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 600, "scope": "s"})
        raise httpx.ReadTimeout("slow")

    sender = InteropSender("https://partner.test", "c", "s", SIGNING,
                           http=httpx.Client(transport=httpx.MockTransport(flaky)), sleep=sleeps.append)
    result = sender.enviar_observacion(message())
    assert (result.status, result.entregado, result.intentos) == (0, False, 3)
    assert "ReadTimeout" in result.mensaje and sleeps == [2.0, 4.0]


def test_a_refused_token_request_is_a_sender_error_with_the_code() -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "bad credentials"})

    sender = InteropSender("https://partner.test", "c", "s", SIGNING,
                           http=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(SenderError) as info:
        sender.obtener_token()
    assert info.value.status == 401 and "invalid_client" in str(info.value)


def test_the_token_is_reused_until_it_is_about_to_expire() -> None:
    clock = [1000.0]
    partner = FakePartner([201, 201, 201])
    http = httpx.Client(transport=httpx.MockTransport(partner))
    sender = InteropSender("https://partner.test", "c", "s", SIGNING, http=http, clock=lambda: clock[0])
    sender.enviar_observacion(message())
    sender.enviar_observacion(message())
    assert partner.token_calls == 1
    clock[0] += 700  # past the 10 minutes
    sender.enviar_observacion(message())
    assert partner.token_calls == 2


# -- end to end against this application's receiver ---------------------------------------------


@pytest.fixture
def live(db, client_as, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    patient = _create_patient(db, client_as, with_vitals=False)
    creds = register(db)
    http = TestClient(app)
    sender = InteropSender(http.base_url.__str__(), creds.client_id, creds.client_secret,
                           creds.signing_secret, http=http, sleep=lambda _s: None)
    return db, patient, sender


def test_the_sender_talks_to_the_real_receiver_201_then_409_for_the_same_resource(live) -> None:
    db, _patient, sender = live
    resource = cliente.build_observation(
        "2339-0", 182, CANARY["document_type"], CANARY["document_number"],
        system=get_settings().federated_system, secret=FEDERATED_SECRET, resource_id="live-1")
    assert sender.enviar_observacion(resource).status == 201
    again = sender.enviar_observacion(resource)
    assert (again.status, again.entregado) == (409, True)
    assert db.scalar(select(Observation).where(Observation.origin == ObservationOrigin.FHIR)) is not None


def test_the_sender_gets_a_403_and_a_422_and_a_202_from_the_real_receiver(db, live) -> None:
    _db, _patient, sender = live
    system = get_settings().federated_system
    unknown = cliente.build_observation("2339-0", 182, "CC", "5550001112", system=system, secret=FEDERATED_SECRET)
    assert sender.enviar_observacion(unknown).status == 202
    bad = cliente.build_observation("2339-0", 5000, "CC", "5550001112", system=system, secret=FEDERATED_SECRET)
    assert sender.enviar_observacion(bad).status == 422

    only_obs = register(db, "equipo2", ("system/Observation.write",))
    narrow = InteropSender(sender.base_url, only_obs.client_id, only_obs.client_secret,
                           only_obs.signing_secret, http=sender._http, sleep=lambda _s: None)
    referral = cliente.build_service_request("m", "CC", "5550001112", system=system, secret=FEDERATED_SECRET)
    result = narrow.enviar_remision(referral)
    assert (result.status, result.entregado) == (403, False)


def test_every_delivery_can_be_audited_as_interop_sent(live) -> None:
    db, _patient, sender = live
    result = sender.enviar_observacion(cliente.build_observation(
        "2339-0", 182, CANARY["document_type"], CANARY["document_number"],
        system=get_settings().federated_system, secret=FEDERATED_SECRET, resource_id=f"a-{uuid.uuid4().hex}"))
    cliente.audit_sent(db, result, base_url="https://partner.test", client_id="equipo1")
    db.expire_all()
    row = db.scalar(select(AuditLog).where(AuditLog.action == AuditAction.INTEROP_SENT))
    meta = row.metadata_json
    assert meta["origen"] == "saludred" and meta["destino"] == "https://partner.test"
    assert meta["recurso"] == "Observation" and meta["codigo_http"] == 201 and meta["entregado"] is True
