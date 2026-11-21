"""Federated patient id (slides 25-26) and the common profile validator (slide 27). Pure logic."""

from __future__ import annotations

import hashlib
import hmac
import re

import pytest

from app.core.config import get_settings
from app.services.interop import federado, perfil
from tests.interop_helpers import FEDERATED_SECRET, observation, referral

# -- federated id -----------------------------------------------------------------------


def test_the_reference_value_of_the_slide_with_the_demo_secret_of_slide_26() -> None:
    """The notes of slide 26 say ``fa0d2121aa10d0c2bb7a5acd8b596f2f`` with the notebook's
    secret. The demo JavaScript of the same slide uses this secret and the number
    ``1.144.123.456``; it reproduces the reference (checked on the ASUS)."""

    demo_secret = "secreto-federado-del-curso-solo-demo"
    reference = "fa0d2121aa10d0c2bb7a5acd8b596f2f"
    assert federado.id_federado("CC", "1.144.123.456", secret=demo_secret) == reference
    assert federado.id_federado("cc", "1144123456", secret=demo_secret) == reference


@pytest.mark.parametrize(
    "number", ["1144123456", "1.144.123.456", "1-144-123-456", " 1 144 123 456 ", "１１４４１２３４５６"]
)
def test_dots_hyphens_spaces_and_fullwidth_digits_give_the_same_id(number: str) -> None:
    assert federado.id_federado("CC", number, secret=FEDERATED_SECRET) == federado.id_federado(
        "CC", "1144123456", secret=FEDERATED_SECRET
    )


def test_the_message_is_type_pipe_number_and_the_id_is_32_hex_of_hmac_sha256() -> None:
    assert federado.normalizar("cc", "1.144.123.456") == "CC|1144123456"
    expected = hmac.new(FEDERATED_SECRET.encode(), b"TI|99", hashlib.sha256).hexdigest()[:32]
    value = federado.id_federado("TI", "99", secret=FEDERATED_SECRET)
    assert value == expected and re.fullmatch(r"[0-9a-f]{32}", value)


def test_another_type_another_number_or_another_secret_changes_the_id() -> None:
    base = federado.id_federado("CC", "1144123456", secret=FEDERATED_SECRET)
    assert federado.id_federado("TI", "1144123456", secret=FEDERATED_SECRET) != base
    assert federado.id_federado("CC", "1144123457", secret=FEDERATED_SECRET) != base
    assert federado.id_federado("CC", "1144123456", secret="another-secret-0123456789") != base


@pytest.mark.parametrize("bad", [("RC", "123"), ("XX", "123"), ("", "123"), ("CC", ""), ("CC", " . - ")])
def test_an_unsupported_type_or_an_empty_number_is_refused(bad: tuple[str, str]) -> None:
    with pytest.raises(federado.FederatedIdError):
        federado.normalizar(*bad)


def test_without_a_secret_the_federated_id_is_disabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "federated_secret", None)
    assert federado.enabled() is False
    with pytest.raises(federado.FederatedDisabledError):
        federado.id_federado("CC", "1")


def test_the_shape_check_only_accepts_32_lowercase_hex() -> None:
    assert federado.looks_like_federated_id("a" * 32)
    for bad in ("A" * 32, "a" * 31, "g" * 32, None, 5):
        assert not federado.looks_like_federated_id(bad)


# -- profile ----------------------------------------------------------------------------

OK = ("CC", "1144123456")


def test_a_conforming_observation_has_no_issues() -> None:
    for code, value in (("2339-0", 182), ("4548-4", 6.5), ("39156-5", 27.3), ("8480-6", 120), ("8462-4", 80)):
        assert perfil.validate_observation(observation(*OK, value=value, code=code)) == [], code


def test_a_misspelled_loinc_system_is_a_code_invalid_issue_on_the_coding_system() -> None:
    resource = observation(*OK)
    resource["code"]["coding"][0]["system"] = "http://loinc.com"
    (issue,) = perfil.validate_observation(resource)
    assert issue.severity == "error" and issue.code == "code-invalid"
    assert issue.expression == ("Observation.code.coding[0].system",)
    assert "http://loinc.org" in issue.diagnostics


def test_a_unit_that_is_not_the_profile_unit_is_refused() -> None:
    resource = observation(*OK)
    resource["valueQuantity"]["code"] = "mmol/L"
    (issue,) = perfil.validate_observation(resource)
    assert issue.expression == ("Observation.valueQuantity.code",) and "mg/dL" in issue.diagnostics


def test_a_wrong_ucum_system_is_refused() -> None:
    resource = observation(*OK)
    resource["valueQuantity"]["system"] = "http://unitsofmeasure.com"
    assert [i.expression for i in perfil.validate_observation(resource)] == [
        ("Observation.valueQuantity.system",)
    ]


@pytest.mark.parametrize(
    ("code", "value"),
    [("2339-0", 5000), ("2339-0", 5), ("4548-4", 55), ("39156-5", 700), ("8480-6", 400), ("8462-4", 5)],
)
def test_an_implausible_value_is_refused(code: str, value: float) -> None:
    (issue,) = perfil.validate_observation(observation(*OK, value=value, code=code))
    assert issue.code == "value" and "implausible" in issue.diagnostics


def test_a_code_outside_the_profile_is_refused() -> None:
    resource = observation(*OK)
    resource["code"]["coding"][0]["code"] = "8867-4"  # heart rate: a real LOINC, not in the profile
    (issue,) = perfil.validate_observation(resource)
    assert issue.code == "code-invalid" and "perfil" in issue.diagnostics


def test_the_subject_must_be_a_federated_identifier_of_the_shared_system() -> None:
    resource = observation(*OK)
    resource["subject"] = {"reference": "Patient/17"}
    (issue,) = perfil.validate_observation(resource)
    assert issue.code == "required" and "identificador federado" in issue.diagnostics

    resource = observation(*OK)
    resource["subject"]["identifier"]["system"] = "urn:otro"
    resource["subject"]["identifier"]["value"] = "1144123456"  # the document itself: refused
    codes = {i.expression for i in perfil.validate_observation(resource)}
    assert codes == {("Observation.subject.identifier.system",), ("Observation.subject.identifier.value",)}


def test_the_value_must_be_a_number_and_status_is_required() -> None:
    resource = observation(*OK)
    resource["valueQuantity"]["value"] = "182"
    resource["status"] = "wat"
    expressions = {i.expression for i in perfil.validate_observation(resource)}
    assert ("Observation.valueQuantity.value",) in expressions and ("Observation.status",) in expressions
    assert perfil.validate_observation({"resourceType": "Patient"})[0].code == "structure"


def test_a_referral_with_snomed_3457005_and_a_reason_conforms() -> None:
    resource = referral(*OK, reason_snomed="44054006", organization_code="IPS-NORTE")
    assert perfil.validate_service_request(resource) == []
    assert perfil.reason_of(resource) == "Control de diabetes tipo 2"
    assert perfil.performer_organization_code(resource) == "IPS-NORTE"


def test_a_referral_without_snomed_3457005_is_refused() -> None:
    resource = referral(*OK)
    resource["code"]["coding"] = [{"system": "http://snomed.info/sct", "code": "11111111"}]
    (issue,) = perfil.validate_service_request(resource)
    assert issue.code == "code-invalid" and "3457005" in issue.diagnostics

    resource["code"]["coding"] = [{"system": "http://snomed.org", "code": "3457005"}]  # wrong system
    (issue,) = perfil.validate_service_request(resource)
    assert "sistema distinto" in issue.diagnostics


def test_a_referral_reason_code_outside_the_snomed_profile_is_refused() -> None:
    resource = referral(*OK, reason_snomed="44054006")
    resource["reasonCode"][0]["coding"][0]["code"] = "99999999"
    (issue,) = perfil.validate_service_request(resource)
    assert issue.expression == ("ServiceRequest.reasonCode[0].coding[0].code",)


def test_a_referral_needs_a_reason_status_and_intent() -> None:
    resource = referral(*OK)
    resource["reasonCode"] = []
    resource["intent"] = "maybe"
    expressions = {i.expression for i in perfil.validate_service_request(resource)}
    assert expressions == {("ServiceRequest.reasonCode",), ("ServiceRequest.intent",)}


def test_the_proposed_ranges_are_marked_to_confirm() -> None:
    assert perfil.PROFILE["4548-4"].to_confirm and perfil.PROFILE["39156-5"].to_confirm
    assert not perfil.PROFILE["2339-0"].to_confirm
    assert perfil.SNOMED_CODES.keys() == {"44054006", "4855003", "38341003", "3457005"}
