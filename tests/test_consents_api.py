# ruff: noqa: F811
"""Consent API and authorized outbound partner sending, using fake transport."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Consent, Patient, Referral
from app.models.enums import AuditAction
from app.services import consents, referrals as referrals_service
from app.services.errors import ForbiddenError, InvalidValueError
from app.services.interop.cliente import SendResult
from app.services.interop.envio import send_to_partner
from tests.factories import make_interop_client
from tests.interop_helpers import FEDERATED_SECRET
from tests.reactive_helpers import add_specialist, audit_rows
from tests.test_ai_reports import _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401
from tests.test_fhir_exchange import record  # noqa: F401

CONSENTS = "/api/v1/consents"
NOW = datetime.now(timezone.utc)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)

UNAUTHORIZED_ROLES = ("paciente.demo", "especialista.norte", "contable.eps", "coordinador.eps")


@pytest.fixture
def scene(db, client_as, ml):
    """One partner, one specialist of another IPS, and a patient of operador.norte."""
    partner = make_interop_client(client_id="socio-demo")
    db.add(partner)
    db.commit()
    patient = _create_patient(db, client_as, with_vitals=False)
    sur = add_specialist(db, username="especialista.sur")
    return db, client_as, partner, patient, sur


def _register(
    client,  # noqa: ANN001
    patient_id: str,
    *,
    cliente_interop_id: str | None = None,
    organizacion_id: str | None = None,
    vigente_desde: str | None = None,
    vigente_hasta: str | None = None,
    **extra: object,
):
    payload: dict[str, object] = {
        "paciente_id": patient_id,
        "cliente_interop_id": cliente_interop_id,
        "organizacion_id": organizacion_id,
        "vigente_desde": vigente_desde or (NOW - DAY).isoformat(),
    }
    if vigente_hasta is not None:
        payload["vigente_hasta"] = vigente_hasta
    payload.update(extra)
    return client.post(CONSENTS, json=payload)


# ---------------------------------------------------------------------------
# Registration (POST /api/v1/consents)
# ---------------------------------------------------------------------------


def test_a_medico_registers_a_partner_consent(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    medico = _user(db, "operador.norte")
    response = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    assert response.status_code == 201, response.text
    body = response.json()
    consent_id = uuid.UUID(body["id"])
    assert body["paciente_id"] == patient["id"]
    assert body["cliente_interop_id"] == "socio-demo"
    assert body["organizacion_id"] is None
    assert body["estado"] == "activa"
    assert body["vigente_hasta"] is None
    assert body["creado_por"] == str(medico.id)
    assert body["revocado_en"] is None and body["revocado_por"] is None

    entries = audit_rows(db, AuditAction.CONSENT_CREATED)
    assert len(entries) == 1
    assert entries[0].entity_id == consent_id
    assert entries[0].user_id == medico.id
    assert entries[0].metadata_json["patient_id"] == patient["id"]
    assert entries[0].metadata_json["interop_client_id"] == "socio-demo"
    assert entries[0].metadata_json["organization_id"] is None


def test_an_admin_registers_an_organization_consent(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    organization_id = _user(db, "operador.norte").organization_id
    response = _register(
        client_as("admin"), patient["id"], organizacion_id=str(organization_id)
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organizacion_id"] == str(organization_id)
    assert body["cliente_interop_id"] is None
    assert body["estado"] == "activa"

    (entry,) = audit_rows(db, AuditAction.CONSENT_CREATED)
    assert entry.user_id == _user(db, "admin").id
    assert entry.metadata_json["organization_id"] == str(organization_id)
    assert entry.metadata_json["interop_client_id"] is None


def test_exactly_one_destination_is_422_and_persists_nothing(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    organization_id = _user(db, "operador.norte").organization_id

    both = _register(
        client_as("operador.norte"),
        patient["id"],
        cliente_interop_id="socio-demo",
        organizacion_id=str(organization_id),
    )
    assert both.status_code == 422
    neither = client_as("operador.norte").post(
        CONSENTS,
        json={"paciente_id": patient["id"], "vigente_desde": (NOW - DAY).isoformat()},
    )
    assert neither.status_code == 422

    assert db.scalar(select(Consent.id)) is None
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


def test_unknown_fields_are_refused_by_the_schema(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    response = _register(
        client_as("operador.norte"),
        patient["id"],
        cliente_interop_id="socio-demo",
        documento="CC-123",
    )
    assert response.status_code == 422
    assert db.scalar(select(Consent.id)) is None


def test_an_overlapping_consent_is_409(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    first = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    assert first.status_code == 201
    second = _register(
        client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo",
        vigente_desde=(NOW - HOUR).isoformat(),
    )
    assert second.status_code == 409
    assert len(db.scalars(select(Consent)).all()) == 1
    assert len(audit_rows(db, AuditAction.CONSENT_CREATED)) == 1


def test_a_window_ending_before_it_starts_is_422(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    response = _register(
        client_as("operador.norte"),
        patient["id"],
        cliente_interop_id="socio-demo",
        vigente_desde=(NOW).isoformat(),
        vigente_hasta=(NOW - HOUR).isoformat(),
    )
    assert response.status_code == 422
    assert db.scalar(select(Consent.id)) is None


def test_unknown_destinations_and_patients_are_404(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    unknown_client = _register(
        client_as("operador.norte"), patient["id"], cliente_interop_id="socio-inexistente"
    )
    assert unknown_client.status_code == 404
    unknown_org = _register(
        client_as("operador.norte"), patient["id"], organizacion_id=str(uuid.uuid4())
    )
    assert unknown_org.status_code == 404
    unknown_patient = _register(
        client_as("operador.norte"), str(uuid.uuid4()), cliente_interop_id="socio-demo"
    )
    assert unknown_patient.status_code == 404
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


def test_an_expired_consent_reads_as_expirada(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    response = _register(
        client_as("operador.norte"),
        patient["id"],
        cliente_interop_id="socio-demo",
        vigente_desde=(NOW - 2 * DAY).isoformat(),
        vigente_hasta=(NOW - DAY).isoformat(),
    )
    assert response.status_code == 201, response.text
    assert response.json()["estado"] == "expirada"

    listed = client_as("admin").get(CONSENTS).json()
    assert listed[0]["estado"] == "expirada"


@pytest.mark.parametrize("username", UNAUTHORIZED_ROLES)
def test_other_roles_cannot_register_consents(scene, username) -> None:
    db, client_as, _partner, patient, _sur = scene
    response = _register(client_as(username), patient["id"], cliente_interop_id="socio-demo")
    assert response.status_code == 403, (username, response.text)
    assert db.scalar(select(Consent.id)) is None
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


# ---------------------------------------------------------------------------
# Revocation (DELETE /api/v1/consents/{consent_id})
# ---------------------------------------------------------------------------


def test_a_medico_revokes_a_consent(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    medico = _user(db, "operador.norte")
    created = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    consent_id = created.json()["id"]

    revoked = client_as("operador.norte").delete(f"{CONSENTS}/{consent_id}")
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["id"] == consent_id
    assert body["estado"] == "revocada"
    assert body["revocado_en"] is not None
    assert body["revocado_por"] == str(medico.id)

    entries = audit_rows(db, AuditAction.CONSENT_REVOKED)
    assert len(entries) == 1
    assert str(entries[0].entity_id) == consent_id
    assert entries[0].user_id == medico.id


def test_revoking_an_unknown_consent_is_404(scene) -> None:
    db, client_as, _partner, _patient, _sur = scene
    response = client_as("operador.norte").delete(f"{CONSENTS}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert audit_rows(db, AuditAction.CONSENT_REVOKED) == []


def test_revoking_twice_is_409(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    created = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    consent_id = created.json()["id"]
    assert client_as("operador.norte").delete(f"{CONSENTS}/{consent_id}").status_code == 200
    assert client_as("operador.norte").delete(f"{CONSENTS}/{consent_id}").status_code == 409
    assert len(audit_rows(db, AuditAction.CONSENT_REVOKED)) == 1


@pytest.mark.parametrize("username", UNAUTHORIZED_ROLES)
def test_other_roles_cannot_revoke_consents(scene, username) -> None:
    db, client_as, _partner, patient, _sur = scene
    created = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    consent_id = created.json()["id"]

    response = client_as(username).delete(f"{CONSENTS}/{consent_id}")
    assert response.status_code == 403, (username, response.text)
    row = db.get(Consent, uuid.UUID(consent_id))
    assert row.revoked_at is None
    assert audit_rows(db, AuditAction.CONSENT_REVOKED) == []


# ---------------------------------------------------------------------------
# Listing (GET /api/v1/consents)
# ---------------------------------------------------------------------------


def test_listing_consents_and_filtering_by_patient(scene) -> None:
    db, client_as, _partner, patient, _sur = scene
    medico = client_as("operador.norte")
    organization_id = _user(db, "operador.norte").organization_id

    first = _register(medico, patient["id"], cliente_interop_id="socio-demo").json()["id"]
    second = _register(medico, patient["id"], organizacion_id=str(organization_id)).json()["id"]

    other_patient = db.scalar(select(Patient.id).where(Patient.id != uuid.UUID(patient["id"])).limit(1))
    assert other_patient is not None
    third = _register(
        client_as("admin"), str(other_patient), cliente_interop_id="socio-demo"
    ).json()["id"]

    everything = client_as("admin").get(CONSENTS)
    assert everything.status_code == 200, everything.text
    assert {row["id"] for row in everything.json()} == {first, second, third}

    filtered = client_as("admin").get(f"{CONSENTS}?paciente_id={patient['id']}")
    assert {row["id"] for row in filtered.json()} == {first, second}

    assert client_as("operador.norte").get(CONSENTS).status_code == 200


@pytest.mark.parametrize("username", UNAUTHORIZED_ROLES)
def test_other_roles_cannot_list_consents(scene, username) -> None:
    db, client_as, _partner, patient, _sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    response = client_as(username).get(CONSENTS)
    assert response.status_code == 403, (username, response.text)


def test_a_consent_of_a_soft_deleted_patient_leaves_the_list(scene) -> None:
    """The list is the clinical surface; the audit log keeps the evidence."""
    db, client_as, _partner, patient, _sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")

    assert (
        client_as("operador.norte").delete(f"/api/v1/patients/{patient['id']}").status_code
        == 204
    )
    assert client_as("admin").get(CONSENTS).json() == []
    # The audit trail is untouched: the consent still exists as evidence.
    assert len(audit_rows(db, AuditAction.CONSENT_CREATED)) == 1
    assert len(db.scalars(select(Consent)).all()) == 1


# ---------------------------------------------------------------------------
# Outbound partner consent gate (service level, fake network sender)
# ---------------------------------------------------------------------------


class FakeSender:
    def __init__(self):
        self.bundles = []

    def enviar_bundle(self, resource):
        self.bundles.append(resource)
        return SendResult("Bundle", 201, True, "Delivered", 1)


def _send(db, patient_id, sender, client_id="socio-demo"):
    return send_to_partner(
        db, actor=_user(db, "operador.norte"), patient_id=uuid.UUID(patient_id),
        client_id=client_id, sender=sender,
    )


def test_partner_send_without_consent_is_audited_and_never_uses_transport(scene) -> None:
    db, _client_as, _partner, patient, _sur = scene
    sender = FakeSender()
    with pytest.raises(ForbiddenError):
        _send(db, patient["id"], sender)
    assert sender.bundles == []
    (entry,) = audit_rows(db, AuditAction.INTEROP_REJECTED)
    assert entry.user_id == _user(db, "operador.norte").id
    assert str(entry.entity_id) == patient["id"]
    assert db.scalar(select(Referral.id)) is None
    assert audit_rows(db, AuditAction.REFERRAL_CREATED) == []


def test_partner_send_with_consent_hands_minimal_bundle_to_sender(scene, monkeypatch) -> None:
    # The federated secret is now required to send: without it the Bundle would carry no patient
    # identifier at all (this test used to pass with exactly that broken Bundle).
    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    db, client_as, _partner, patient, _sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")

    sender = FakeSender()
    assert _send(db, patient["id"], sender).entregado
    assert len(sender.bundles) == 1
    bundle = sender.bundles[0]
    assert bundle["type"] == "collection"
    first = bundle["entry"][0]["resource"]
    assert set(first) <= {"resourceType", "identifier"}
    assert first["identifier"][0]["value"], "the Patient carries the federated id"
    assert len(audit_rows(db, AuditAction.INTEROP_SENT)) == 1


def test_a_patient_without_federated_id_fails_fast_before_anything_is_sent(scene, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "federated_secret", None)  # no secret: no id can be computed
    db, client_as, _partner, patient, _sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")

    persisted: list[bool] = []
    sender = FakeSender()
    with pytest.raises(InvalidValueError) as raised:
        send_to_partner(
            db, actor=_user(db, "operador.norte"), patient_id=uuid.UUID(patient["id"]),
            client_id="socio-demo", sender=sender, before_send=lambda: persisted.append(True),
        )
    assert "identificador federado" in str(raised.value)
    assert sender.bundles == [] and persisted == []  # nothing was persisted or sent
    (entry,) = audit_rows(db, AuditAction.INTEROP_REJECTED)
    assert entry.metadata_json["reason"] == "no_federated_id" and entry.metadata_json["direction"] == "outbound"
    assert str(entry.entity_id) == patient["id"]
    assert audit_rows(db, AuditAction.INTEROP_SENT) == []


def test_an_in_network_referral_never_touches_the_gate(scene) -> None:
    db, _client_as, _partner, patient, sur = scene
    referral = referrals_service.create(
        db, actor=_user(db, "operador.norte"), patient_id=uuid.UUID(patient["id"]),
        specialist_id=sur.id, motivo="Valoracion cardiologica", idempotency_key="local-replay",
    )
    replay = referrals_service.create(
        db, actor=_user(db, "operador.norte"), patient_id=uuid.UUID(patient["id"]),
        specialist_id=sur.id, motivo="Valoracion cardiologica", idempotency_key="local-replay",
    )
    assert replay.id == referral.id
    assert db.get(Referral, referral.id) is not None
    assert len(audit_rows(db, AuditAction.REFERRAL_CREATED)) == 1


def test_a_consent_for_another_partner_does_not_open_the_gate(scene) -> None:
    db, client_as, _partner, patient, sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    sender = FakeSender()
    with pytest.raises(ForbiddenError):
        _send(db, patient["id"], sender, client_id="socio-otro")
    assert sender.bundles == []
    assert db.scalar(select(Referral.id)) is None


def test_a_consent_for_another_patient_does_not_open_the_gate(scene) -> None:
    db, client_as, _partner, patient, sur = scene
    other_patient = db.scalar(select(Patient.id).where(Patient.id != uuid.UUID(patient["id"])).limit(1))
    assert other_patient is not None
    consents.register_consent(
        db,
        actor=_user(db, "admin"),
        patient_id=other_patient,
        interop_client_id="socio-demo",
        valid_from=NOW - DAY,
    )
    sender = FakeSender()
    with pytest.raises(ForbiddenError):
        _send(db, patient["id"], sender)
    assert sender.bundles == []
    assert db.scalar(select(Referral.id)) is None


def test_another_ips_cannot_register_revoke_list_or_send(scene, monkeypatch):
    db, client_as, _partner, patient, sur = scene
    created = _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    consent_id = created.json()["id"]
    # Keep the operator role but move the actor to the other institution.
    operator = _user(db, "operador.norte")
    operator.organization_id = sur.organization_id
    db.commit()
    client = client_as("operador.norte")
    assert _register(client, patient["id"], cliente_interop_id="socio-demo").status_code == 403
    assert client.delete(f"{CONSENTS}/{consent_id}").status_code == 403
    assert client.get(CONSENTS).json() == []
    assert client.get(CONSENTS, params={"paciente_id": patient["id"]}).json() == []
    sender = FakeSender()

    def gate_must_not_run(*args, **kwargs):
        pytest.fail("Consent gate ran before the scope check")

    monkeypatch.setattr(consents, "require_valid_consent", gate_must_not_run)
    with pytest.raises(ForbiddenError):
        _send(db, patient["id"], sender)
    assert sender.bundles == []


def test_naive_consent_timestamp_is_422(scene):
    db, client_as, _partner, patient, _sur = scene
    response = _register(client_as("operador.norte"), patient["id"],
                         cliente_interop_id="socio-demo", vigente_desde="2026-01-01T00:00:00")
    assert response.status_code == 422
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


def test_non_utc_consent_timestamp_is_normalized(scene):
    db, client_as, _partner, patient, _sur = scene
    start = (NOW - DAY).astimezone(timezone(timedelta(hours=-5)))
    response = _register(client_as("operador.norte"), patient["id"],
                         cliente_interop_id="socio-demo", vigente_desde=start.isoformat())
    assert response.status_code == 201, response.text
    row = db.get(Consent, uuid.UUID(response.json()["id"]))
    assert consents._naive_utc(row.valid_from) == consents._naive_utc(NOW - DAY)


@pytest.mark.parametrize("limit", [0, 201])
def test_consent_list_limit_outside_bounds_is_422(scene, limit):
    _db, client_as, *_ = scene
    assert client_as("admin").get(CONSENTS, params={"limit": limit}).status_code == 422


def test_consent_list_honors_explicit_limit(scene):
    _db, client_as, _partner, patient, _sur = scene
    _register(client_as("admin"), patient["id"], cliente_interop_id="socio-demo")
    _register(client_as("admin"), patient["id"],
              organizacion_id=str(_user(_db, "operador.norte").organization_id))
    assert len(client_as("admin").get(CONSENTS, params={"limit": 1}).json()) == 1


def test_inactive_partner_is_rejected_by_registration_and_send(scene):
    db, client_as, partner, patient, _sur = scene
    _register(client_as("admin"), patient["id"], cliente_interop_id="socio-demo")
    partner.is_active = False
    db.commit()
    assert _register(client_as("admin"), patient["id"], cliente_interop_id="socio-demo").status_code == 403
    sender = FakeSender()
    with pytest.raises(ForbiddenError):
        _send(db, patient["id"], sender)
    assert sender.bundles == []
    assert len(audit_rows(db, AuditAction.INTEROP_REJECTED)) == 1


def test_partner_send_exports_only_approved_reports(record, monkeypatch):
    db, client_as, patient, approved = record
    # Without a federated id nothing leaves (fail fast), so the secret must be set.
    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    db.add(make_interop_client(client_id="socio-demo"))
    db.commit()
    assert _register(client_as("operador.norte"), patient["id"],
                     cliente_interop_id="socio-demo").status_code == 201
    sender = FakeSender()
    _send(db, patient["id"], sender)
    resources = [entry["resource"] for entry in sender.bundles[0]["entry"]]
    reports = [resource for resource in resources if resource["resourceType"] == "RiskAssessment"]
    assert [report["id"] for report in reports] == [approved]
    assert all(resource["resourceType"] != "Encounter" for resource in resources)


def test_partner_service_uses_existing_sender_without_network(scene, monkeypatch):
    from tests.test_interop_sender import FakePartner, sender_for

    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    _db, client_as, _partner, patient, _sur = scene
    _register(client_as("operador.norte"), patient["id"], cliente_interop_id="socio-demo")
    partner = FakePartner([201])
    sender = sender_for(partner)
    result = _send(_db, patient["id"], sender)
    assert result.entregado
    assert len(partner.interop_calls) == 1
    assert partner.interop_calls[0].url.path == "/interop/Bundle"
