# ruff: noqa: F811
"""Epic 7 -- referrals (R08): create, access grant, actions, FHIR ServiceRequest."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.fhir import mappers
from app.models import Referral
from app.models.enums import AuditAction, ReferralStatus
from app.services import fhir_autosync, pacs
from tests.fhir_helpers import FakeFhir
from tests.reactive_helpers import (
    REFERRALS,
    add_specialist,
    actors,
    add_study,
    audit_rows,
    refer,
)
from tests.test_ai_reports import _analyze, _create_patient, ml  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


PACS_STUDY = "dddddddd-11111111-22222222-33333333-44444444"
PACS_INSTANCE = "eeeeeeee-11111111-22222222-33333333-44444444"


@pytest.fixture
def scene(db, client_as, ml, monkeypatch):
    monkeypatch.setattr(
        pacs, "list_instances", lambda _study: [{"instance_id": PACS_INSTANCE, "number": 1}]
    )
    patient = _create_patient(db, client_as)
    sur = add_specialist(db, username="especialista.sur", org_code="IPS-SUR")
    otro = add_specialist(db, username="especialista.otro", org_code="IPS-SUR")
    return db, actors(client_as), patient, sur, otro


def _referral_id(response) -> str:  # noqa: ANN001
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_a_medico_refers_a_patient_and_gets_the_id(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    response = refer(client_as("operador.norte"), patient["id"], sur, "Dolor toracico")
    assert response.status_code == 201, response.text
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["estado"] == "pendiente" and body["paciente_id"] == patient["id"]
    assert body["motivo"] == "Dolor toracico"
    assert body["especialista_id"] == str(sur.id) and body["especialista_nombre"] == sur.full_name
    assert body["especialista_organizacion"] == "Hospital Sur"
    assert body["remitente_id"] == str(_user(db, "operador.norte").id)
    assert body["paciente_nombre"].startswith("Zacarias")
    assert body["aceptada_en"] is None and body["completada_en"] is None


@pytest.mark.parametrize(
    "username",
    ["paciente.demo", "contable.eps", "coordinador.eps", "admin", "especialista.norte"],
)
def test_only_a_medico_can_create_referrals(scene, username) -> None:
    _db, client_as, patient, sur, _otro = scene
    response = refer(client_as(username), patient["id"], sur)
    assert response.status_code == 403, (username, response.text)


def test_the_medico_needs_access_to_the_patient(scene) -> None:
    """operador.sur has no encounter with this patient: 403, not a silent referral."""

    _db, client_as, patient, sur, _otro = scene
    assert refer(client_as("operador.sur"), patient["id"], sur).status_code == 403


def test_the_target_must_be_an_active_specialist(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    medico = client_as("operador.norte")
    not_a_specialist = _user(db, "operador.sur")
    assert refer(medico, patient["id"], not_a_specialist).status_code == 422
    assert refer(medico, patient["id"], _user(db, "admin")).status_code == 422

    disabled = add_specialist(db, username="esp.inactivo", active=False)
    locked = add_specialist(db, username="esp.bloqueado", locked=True)
    assert refer(medico, patient["id"], disabled).status_code == 422
    assert refer(medico, patient["id"], locked).status_code == 422

    ghost = medico.post(
        REFERRALS,
        json={"paciente_id": patient["id"], "especialista_id": str(uuid.uuid4()), "motivo": "x"},
    )
    assert ghost.status_code == 404
    missing_patient = medico.post(
        REFERRALS,
        json={"paciente_id": str(uuid.uuid4()), "especialista_id": str(sur.id), "motivo": "x"},
    )
    assert missing_patient.status_code == 404


def test_a_referral_across_ips_is_allowed_and_a_blank_reason_is_not(scene) -> None:
    _db, client_as, patient, sur, _otro = scene
    medico = client_as("operador.norte")
    assert refer(medico, patient["id"], sur).status_code == 201  # Norte -> Sur
    assert refer(medico, patient["id"], sur, "   ").status_code == 422
    assert medico.post(REFERRALS, json={"paciente_id": patient["id"]}).status_code == 422


def test_creation_is_audited(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    (row,) = audit_rows(db, AuditAction.REFERRAL_CREATED)
    assert str(row.entity_id) == referral_id
    assert row.metadata_json["patient_id"] == patient["id"]
    assert row.metadata_json["specialist_user_id"] == str(sur.id)
    assert row.user_id == _user(db, "operador.norte").id


# ---------------------------------------------------------------------------
# The access grant
# ---------------------------------------------------------------------------


def _closed_to(client, patient_id: str, report_id: str, study_id: uuid.UUID) -> None:  # noqa: ANN001
    assert client.get(f"/api/v1/patients/{patient_id}/clinical-record").status_code == 403
    assert client.get(f"/api/v1/patients/{patient_id}/clinical-record.pdf").status_code == 403
    assert client.get(f"/api/v1/patients/{patient_id}/fhir-bundle").status_code == 403
    assert client.get(f"/api/v1/observations?patient_id={patient_id}").json()["total"] == 0
    assert client.get(f"/api/v1/patients/{patient_id}/encounters").json() == []
    assert client.get(f"/api/v1/encounters?patient_id={patient_id}").json()["total"] == 0
    assert client.get(f"/api/v1/patients/{patient_id}/imaging-studies").json()["total"] == 0
    assert client.get(f"/api/v1/patients/{patient_id}/imagenes").json() == {"imagenes": []}
    assert client.get(f"/api/v1/imaging-studies?patient_id={patient_id}").json()["total"] == 0
    assert client.get(f"/api/v1/imaging-studies/{study_id}").status_code == 403
    assert client.get(f"/api/v1/imaging-studies/{study_id}/images").status_code == 404
    assert client.get(f"/api/v1/ai-reports/{report_id}").status_code == 403
    assert client.get(f"/api/v1/ai-reports?patient_id={patient_id}").json() == []


def _open_to(client, patient_id: str, report_id: str, study_id: uuid.UUID) -> None:  # noqa: ANN001
    record = client.get(f"/api/v1/patients/{patient_id}/clinical-record")
    assert record.status_code == 200, record.text
    assert len(record.json()["observaciones"]) == 7
    assert [r["id"] for r in record.json()["reportes_ia"]] == [report_id]
    pdf = client.get(f"/api/v1/patients/{patient_id}/clinical-record.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert client.get(f"/api/v1/patients/{patient_id}/fhir-bundle").status_code == 200
    assert client.get(f"/api/v1/observations?patient_id={patient_id}").json()["total"] == 7
    assert len(client.get(f"/api/v1/patients/{patient_id}/encounters").json()) == 1
    assert client.get(f"/api/v1/encounters?patient_id={patient_id}").json()["total"] == 1
    assert client.get(f"/api/v1/patients/{patient_id}/imaging-studies").json()["total"] == 1
    images = client.get(f"/api/v1/patients/{patient_id}/imagenes").json()["imagenes"]
    assert [(i["id"], i["estudio_id"]) for i in images] == [(PACS_INSTANCE, str(study_id))]
    assert client.get(f"/api/v1/imaging-studies?patient_id={patient_id}").json()["total"] == 1
    assert client.get(f"/api/v1/imaging-studies/{study_id}").status_code == 200
    assert client.get(f"/api/v1/imaging-studies/{study_id}/images").status_code == 200
    assert client.get(f"/api/v1/ai-reports/{report_id}").status_code == 200
    assert [r["id"] for r in client.get(f"/api/v1/ai-reports?patient_id={patient_id}").json()] == [
        report_id
    ]


def _record_with_an_approved_report(db, client_as, patient):  # noqa: ANN001
    medico = client_as("operador.norte")
    report = _analyze(medico, patient["id"]).json()
    assert medico.post(f"/api/v1/ai-reports/{report['id']}/approve").status_code == 200
    org = _user(db, "operador.norte").organization_id
    study = add_study(db, patient["id"], org)
    study.pacs_study_id = PACS_STUDY  # the pixels "live" in the (faked) PACS
    db.commit()
    return report["id"], study.id


def test_a_referral_opens_the_whole_record_to_the_specialist(scene) -> None:
    db, client_as, patient, sur, otro = scene
    report_id, study_id = _record_with_an_approved_report(db, client_as, patient)
    specialist, other = client_as("especialista.sur"), client_as("especialista.otro")

    _closed_to(specialist, patient["id"], report_id, study_id)
    _closed_to(other, patient["id"], report_id, study_id)

    assert refer(client_as("operador.norte"), patient["id"], sur).status_code == 201

    _open_to(specialist, patient["id"], report_id, study_id)
    # Another specialist of the same IPS gets nothing: the grant is personal.
    _closed_to(other, patient["id"], report_id, study_id)
    # And the medico of the other IPS still has no access of its own.
    assert client_as("operador.sur").get(
        f"/api/v1/patients/{patient['id']}/clinical-record"
    ).status_code == 403


def test_a_rejected_referral_grants_nothing(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    report_id, study_id = _record_with_an_approved_report(db, client_as, patient)
    specialist = client_as("especialista.sur")
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    _open_to(specialist, patient["id"], report_id, study_id)

    rejected = specialist.post(f"{REFERRALS}/{referral_id}/reject", json={"motivo": "No es mi area"})
    assert rejected.status_code == 200 and rejected.json()["estado"] == "rechazada"

    _closed_to(specialist, patient["id"], report_id, study_id)


@pytest.mark.parametrize("final_state", ["pendiente", "aceptada", "completada"])
def test_pending_accepted_and_completed_referrals_all_grant_access(scene, final_state) -> None:
    db, client_as, patient, sur, _otro = scene
    report_id, study_id = _record_with_an_approved_report(db, client_as, patient)
    specialist = client_as("especialista.sur")
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    if final_state in ("aceptada", "completada"):
        assert specialist.post(f"{REFERRALS}/{referral_id}/accept").status_code == 200
    if final_state == "completada":
        assert specialist.post(
            f"{REFERRALS}/{referral_id}/report", json={"informe": "Sin hallazgos"}
        ).status_code == 200
    _open_to(specialist, patient["id"], report_id, study_id)


def test_the_specialist_can_upload_images_for_a_referred_patient(scene) -> None:
    """The existing imaging endpoints work for the specialist's own IPS; the grant
    is what lets them see the patient's studies from the other IPS next to them."""

    db, client_as, patient, sur, _otro = scene
    assert refer(client_as("operador.norte"), patient["id"], sur).status_code == 201
    specialist = client_as("especialista.sur")
    org = _user(db, "especialista.sur").organization_id
    created = specialist.post(
        "/api/v1/imaging-studies",
        json={
            "patient_id": patient["id"],
            "organization_id": str(org),
            "study_instance_uid": "1.2.840.99999.7.7.7",
            "accession_number": "ACC-ESP-1",
            "modality": "DX",
            "body_site": "Torax",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    listing = specialist.get(f"/api/v1/patients/{patient['id']}/imaging-studies").json()
    assert listing["total"] == 1


# ---------------------------------------------------------------------------
# Actions and state machine
# ---------------------------------------------------------------------------


def test_accept_then_report_completes_the_referral(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    specialist = client_as("especialista.sur")

    accepted = specialist.post(f"{REFERRALS}/{referral_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["estado"] == "aceptada" and accepted.json()["aceptada_en"]

    done = specialist.post(
        f"{REFERRALS}/{referral_id}/report", json={"informe": "Control en 3 meses"}
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["estado"] == "completada" and body["informe"] == "Control en 3 meses"
    assert body["completada_en"]
    # The sender reads the report in their own list.
    sent = client_as("operador.norte").get(f"{REFERRALS}?rol=enviadas").json()
    assert sent[0]["informe"] == "Control en 3 meses"

    row = db.get(Referral, uuid.UUID(referral_id))
    assert row.status == ReferralStatus.COMPLETED and row.informe_especialista
    for action in (
        AuditAction.REFERRAL_CREATED, AuditAction.REFERRAL_ACCEPTED, AuditAction.REFERRAL_COMPLETED
    ):
        assert [str(r.entity_id) for r in audit_rows(db, action)] == [referral_id], action


def test_invalid_transitions_are_409(scene) -> None:
    _db, client_as, patient, sur, _otro = scene
    medico, specialist = client_as("operador.norte"), client_as("especialista.sur")
    referral_id = _referral_id(refer(medico, patient["id"], sur))
    base = f"{REFERRALS}/{referral_id}"

    # report needs an accepted referral
    assert specialist.post(f"{base}/report", json={"informe": "x"}).status_code == 409
    assert specialist.post(f"{base}/accept").status_code == 200
    assert specialist.post(f"{base}/accept").status_code == 409
    # reject only from pendiente
    assert specialist.post(f"{base}/reject", json={"motivo": "tarde"}).status_code == 409
    assert specialist.post(f"{base}/report", json={"informe": "ok"}).status_code == 200
    assert specialist.post(f"{base}/report", json={"informe": "otra vez"}).status_code == 409
    assert specialist.post(f"{base}/accept").status_code == 409


def test_reject_needs_a_reason_and_is_final(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    specialist = client_as("especialista.sur")
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    base = f"{REFERRALS}/{referral_id}"

    assert specialist.post(f"{base}/reject", json={}).status_code == 422
    assert specialist.post(f"{base}/reject", json={"motivo": "  "}).status_code == 422
    rejected = specialist.post(f"{base}/reject", json={"motivo": "Fuera de mi especialidad"})
    assert rejected.status_code == 200
    assert rejected.json()["motivo_rechazo"] == "Fuera de mi especialidad"
    assert rejected.json()["rechazada_en"]
    assert specialist.post(f"{base}/accept").status_code == 409
    assert specialist.post(f"{base}/report", json={"informe": "x"}).status_code == 409
    (row,) = audit_rows(db, AuditAction.REFERRAL_REJECTED)
    assert row.metadata_json["motivo_rechazo"] == "Fuera de mi especialidad"


def test_a_blank_report_is_refused(scene) -> None:
    _db, client_as, patient, sur, _otro = scene
    specialist = client_as("especialista.sur")
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    specialist.post(f"{REFERRALS}/{referral_id}/accept")
    assert specialist.post(f"{REFERRALS}/{referral_id}/report", json={"informe": " "}).status_code == 422


@pytest.mark.parametrize(
    "username", ["operador.norte", "especialista.otro", "admin", "paciente.demo", "contable.eps"]
)
def test_only_the_addressee_answers(scene, username) -> None:
    _db, client_as, patient, sur, _otro = scene
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    client = client_as(username)
    base = f"{REFERRALS}/{referral_id}"
    assert client.post(f"{base}/accept").status_code == 403
    assert client.post(f"{base}/reject", json={"motivo": "no"}).status_code == 403
    assert client.post(f"{base}/report", json={"informe": "no"}).status_code == 403
    # still pending: nothing changed
    assert client_as("especialista.sur").get(base).json()["estado"] == "pendiente"


def test_an_unknown_referral_is_404(scene) -> None:
    _db, client_as, _patient, _sur, _otro = scene
    assert client_as("especialista.sur").post(f"{REFERRALS}/{uuid.uuid4()}/accept").status_code == 404


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_the_specialist_sees_received_and_the_medico_sent(scene) -> None:
    db, client_as, patient, sur, otro = scene
    medico = client_as("operador.norte")
    first = _referral_id(refer(medico, patient["id"], sur, "primera"))
    second = _referral_id(refer(medico, patient["id"], otro, "segunda"))
    client_as("especialista.sur").post(f"{REFERRALS}/{first}/accept")

    received = client_as("especialista.sur").get(REFERRALS).json()
    assert [r["id"] for r in received] == [first]
    assert received[0]["paciente_id"] == patient["id"] and received[0]["estado"] == "aceptada"
    assert received[0]["paciente_nombre"] and received[0]["motivo"] == "primera"
    assert received[0]["remitente_nombre"] and received[0]["especialista_nombre"]

    sent = medico.get(REFERRALS).json()
    assert {r["id"] for r in sent} == {first, second}
    assert [r["id"] for r in medico.get(f"{REFERRALS}?estado=pendiente").json()] == [second]
    assert [r["id"] for r in medico.get(f"{REFERRALS}?rol=enviadas&estado=aceptada").json()] == [first]
    assert medico.get(f"{REFERRALS}?rol=recibidas").json() == []
    assert client_as("especialista.sur").get(f"{REFERRALS}?rol=enviadas").json() == []
    assert medico.get(f"{REFERRALS}?rol=otra").status_code == 422
    assert medico.get(f"{REFERRALS}?estado=nada").status_code == 422

    # Another medico and another specialist see none of them.
    assert client_as("operador.sur").get(REFERRALS).json() == []
    assert client_as("especialista.norte").get(REFERRALS).json() == []
    # The admin sees all of them.
    assert {r["id"] for r in client_as("admin").get(REFERRALS).json()} == {first, second}


@pytest.mark.parametrize("username", ["paciente.demo", "contable.eps", "coordinador.eps"])
def test_other_roles_cannot_list_referrals(scene, username) -> None:
    _db, client_as, patient, sur, _otro = scene
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    client = client_as(username)
    assert client.get(REFERRALS).status_code == 403
    assert client.get(f"{REFERRALS}/{referral_id}").status_code == 403
    assert client.get(f"{REFERRALS}/specialists").status_code == 403


def test_a_referral_of_a_deleted_patient_leaves_the_list(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    assert client_as("operador.norte").delete(f"/api/v1/patients/{patient['id']}").status_code == 204
    assert client_as("especialista.sur").get(REFERRALS).json() == []
    assert client_as("operador.norte").get(REFERRALS).json() == []


def test_get_one_referral_is_for_its_two_parties_and_the_admin(scene) -> None:
    _db, client_as, patient, sur, _otro = scene
    referral_id = _referral_id(refer(client_as("operador.norte"), patient["id"], sur))
    for username in ("operador.norte", "especialista.sur", "admin"):
        assert client_as(username).get(f"{REFERRALS}/{referral_id}").status_code == 200, username
    for username in ("operador.sur", "especialista.otro"):
        assert client_as(username).get(f"{REFERRALS}/{referral_id}").status_code == 404, username


def test_the_specialist_picker(scene) -> None:
    db, client_as, _patient, sur, _otro = scene
    add_specialist(db, username="esp.inactivo", active=False)
    add_specialist(db, username="esp.bloqueado", locked=True)
    picker = client_as("operador.norte").get(f"{REFERRALS}/specialists")
    assert picker.status_code == 200, picker.text
    ids = {row["id"] for row in picker.json()}
    assert str(sur.id) in ids and str(_user(db, "especialista.norte").id) in ids
    assert str(_user(db, "esp.inactivo").id) not in ids
    assert str(_user(db, "esp.bloqueado").id) not in ids
    assert str(_user(db, "operador.norte").id) not in ids
    assert set(picker.json()[0]) == {"id", "nombre", "organizacion"}
    assert client_as("admin").get(f"{REFERRALS}/specialists").status_code == 200
    assert client_as("especialista.norte").get(f"{REFERRALS}/specialists").status_code == 403


# ---------------------------------------------------------------------------
# FHIR: ServiceRequest
# ---------------------------------------------------------------------------


@pytest.fixture
def hapi(db, monkeypatch):
    fake = FakeFhir()
    monkeypatch.setattr(get_settings(), "fhir_auto_sync", True)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    fhir_autosync.configure(session_factory=factory, client_factory=lambda: fake)
    yield fake
    fhir_autosync.configure()


def _service_requests(hapi: FakeFhir) -> list[dict]:
    return list(hapi.store.get("ServiceRequest", {}).values())


def test_every_referral_state_is_synced_as_a_servicerequest(scene, hapi) -> None:
    db, client_as, patient, sur, otro = scene
    medico, specialist = client_as("operador.norte"), client_as("especialista.sur")

    referral_id = _referral_id(refer(medico, patient["id"], sur, "Dolor toracico"))
    assert ("PUT", "ServiceRequest") in hapi.calls
    (resource,) = _service_requests(hapi)
    assert resource["status"] == "active" and resource["intent"] == "order"
    assert resource["reasonCode"] == [{"text": "Dolor toracico"}]
    assert resource["subject"]["reference"].startswith("Patient/")
    assert resource["requester"]["display"] == _user(db, "operador.norte").full_name
    assert resource["performer"][0]["display"] == sur.full_name
    row = db.get(Referral, uuid.UUID(referral_id))
    assert row.fhir_resource_id == resource["id"]

    specialist.post(f"{REFERRALS}/{referral_id}/accept")
    specialist.post(f"{REFERRALS}/{referral_id}/report", json={"informe": "ok"})
    (resource,) = _service_requests(hapi)  # updated in place, never duplicated
    assert resource["status"] == "completed"

    rejected_id = _referral_id(refer(medico, patient["id"], otro))
    client_as("especialista.otro").post(f"{REFERRALS}/{rejected_id}/reject", json={"motivo": "no"})
    statuses = sorted(r["status"] for r in _service_requests(hapi))
    assert statuses == ["completed", "revoked"]
    # Every outbound resource carries the echo marker the rest-hook ignores.
    for resource in _service_requests(hapi):
        assert {"system": "urn:saludred:identifier:origin", "code": "saludred"} in [
            {k: t[k] for k in ("system", "code")} for t in resource["meta"]["tag"]
        ]


def test_a_fhir_outage_never_fails_the_referral(scene, hapi) -> None:
    _db, client_as, patient, sur, _otro = scene
    hapi.down = True
    assert refer(client_as("operador.norte"), patient["id"], sur).status_code == 201


def test_nothing_is_pushed_when_auto_sync_is_off(scene) -> None:
    _db, client_as, patient, sur, _otro = scene
    fake = FakeFhir()
    fhir_autosync.configure(client_factory=lambda: fake)
    try:
        assert refer(client_as("operador.norte"), patient["id"], sur).status_code == 201
    finally:
        fhir_autosync.configure()
    assert fake.calls == []


def _referral(status: ReferralStatus) -> Referral:
    return Referral(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        referring_user_id=uuid.uuid4(),
        specialist_user_id=uuid.uuid4(),
        motivo="Dolor toracico",
        status=status,
        created_at=datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("status", "fhir_status"),
    [
        (ReferralStatus.PENDING, "active"),
        (ReferralStatus.ACCEPTED, "active"),
        (ReferralStatus.COMPLETED, "completed"),
        (ReferralStatus.REJECTED, "revoked"),
    ],
)
def test_the_servicerequest_mapper(status, fhir_status) -> None:
    referral = _referral(status)
    resource = mappers.referral_to_fhir(
        referral,
        identifier_system="urn:saludred:identifier",
        patient_ref="Patient/123",
        requester_name="Dra. Remitente",
        performer_name="Dr. Especialista",
    )
    assert resource["resourceType"] == "ServiceRequest"
    assert resource["status"] == fhir_status and resource["intent"] == "order"
    assert resource["subject"] == {"reference": "Patient/123"}
    assert resource["requester"]["display"] == "Dra. Remitente"
    assert resource["requester"]["identifier"]["value"] == str(referral.referring_user_id)
    assert resource["performer"][0]["display"] == "Dr. Especialista"
    assert resource["reasonCode"] == [{"text": "Dolor toracico"}]
    assert resource["identifier"] == [
        {"system": "urn:saludred:identifier:referrals", "value": str(referral.id)}
    ]
    assert resource["authoredOn"].startswith("2026-10-03T09:00")
    assert mappers.is_own_resource(resource, "urn:saludred:identifier")


def test_referral_rows_are_in_the_database(scene) -> None:
    db, client_as, patient, sur, _otro = scene
    refer(client_as("operador.norte"), patient["id"], sur)
    assert len(list(db.scalars(select(Referral)))) == 1
