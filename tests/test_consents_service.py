# ruff: noqa: F811
"""WU2 — the consent service and the destination gate (R32).

Service-level tests: registration with query-level overlap enforcement, explicit
revocation, listing, and the ``require_valid_consent`` gate that E1's outbound
``InteropSender`` will consume. Every test runs against the disposable in-memory
SQLite seeded by ``tests.test_bed_rules.db`` (the same fixture the referral
tests use); no real database is ever touched.

The gate contract is exactly the one in the design:
``require_valid_consent(db, *, patient_id, destination)`` where ``destination``
is ``("organization", <uuid>)`` or ``("interop_client", <business client_id>)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect, select

from app.models import Consent, Encounter, Organization, Patient
from app.models.enums import AuditAction, ConsentStatus
from app.services import audit_chain, consents
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
)
from tests.factories import make_consent, make_interop_client
from tests.reactive_helpers import audit_rows
from tests.test_bed_rules import _user, db  # noqa: F401

NOW = datetime.now(timezone.utc)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


@pytest.fixture
def scene(db):  # noqa: ANN001
    """One partner, one patient and one IPS of the seed, ready to consent to."""

    partner = make_interop_client(client_id="socio-demo")
    db.add(partner)
    db.commit()
    organization_id = db.scalar(
        select(Organization.id).where(Organization.code == "IPS-NORTE").limit(1)
    )
    patient_id = db.scalar(select(Encounter.patient_id).where(
        Encounter.organization_id == organization_id, Encounter.deleted_at.is_(None)
    ).limit(1))
    assert patient_id is not None and organization_id is not None
    return db, partner, patient_id, organization_id


def _medico(db):  # noqa: ANN001
    return _user(db, "operador.norte")


def _admin(db):  # noqa: ANN001
    return _user(db, "admin")


def _register_partner(db, scene, **overrides):  # noqa: ANN001
    _db, partner, patient_id, _org = scene
    values = dict(
        actor=_medico(db),
        patient_id=patient_id,
        interop_client_id=partner.client_id,
        valid_from=NOW - 10 * DAY,
    )
    values.update(overrides)
    return consents.register_consent(db, **values)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_a_medico_registers_a_consent_for_a_partner_destination(scene) -> None:
    db, partner, patient_id, _org = scene
    medico = _medico(db)
    consent = consents.register_consent(
        db,
        actor=medico,
        patient_id=patient_id,
        interop_client_id=partner.client_id,
        valid_from=NOW - DAY,
    )
    assert consent.id is not None
    assert consent.patient_id == patient_id
    assert consent.interop_client_id == partner.id  # business id resolved to the row
    assert consent.organization_id is None
    assert consent.valid_until is None  # indefinite until revoked
    assert consent.created_by == medico.id
    assert consent.revoked_at is None and consent.revoked_by is None

    entries = audit_rows(db, AuditAction.CONSENT_CREATED)
    assert len(entries) == 1
    assert entries[0].user_id == medico.id
    assert entries[0].entity_type == "consents"
    assert entries[0].entity_id == consent.id
    assert entries[0].metadata_json["patient_id"] == str(patient_id)
    assert entries[0].metadata_json["interop_client_id"] == "socio-demo"
    assert entries[0].metadata_json["organization_id"] is None


def test_a_consent_can_bind_an_in_network_organization(scene) -> None:
    db, _partner, patient_id, organization_id = scene
    medico = _medico(db)
    consent = consents.register_consent(
        db,
        actor=medico,
        patient_id=patient_id,
        organization_id=organization_id,
        valid_from=NOW - DAY,
    )
    assert consent.organization_id == organization_id
    assert consent.interop_client_id is None
    entries = audit_rows(db, AuditAction.CONSENT_CREATED)
    assert len(entries) == 1
    assert entries[0].metadata_json["organization_id"] == str(organization_id)


@pytest.mark.parametrize(
    "organization_id, interop_client_id",
    [
        ("both", "socio-demo"),
        (None, None),
    ],
)
def test_exactly_one_destination_is_enforced_by_the_service(
    scene, organization_id, interop_client_id
) -> None:
    db, _partner, patient_id, organization_id_of_scene = scene
    org_value = (
        organization_id_of_scene if organization_id == "both" else organization_id
    )
    with pytest.raises(InvalidValueError):
        consents.register_consent(
            db,
            actor=_medico(db),
            patient_id=patient_id,
            organization_id=org_value,
            interop_client_id=interop_client_id,
            valid_from=NOW - DAY,
        )
    assert db.scalar(select(Consent.id)) is None  # nothing persisted
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []  # and not audited


def test_an_unknown_destination_is_rejected(scene) -> None:
    db, _partner, patient_id, _org = scene
    with pytest.raises(NotFoundError):
        consents.register_consent(
            db,
            actor=_medico(db),
            patient_id=patient_id,
            interop_client_id="socio-inexistente",
            valid_from=NOW - DAY,
        )
    with pytest.raises(NotFoundError):
        consents.register_consent(
            db,
            actor=_medico(db),
            patient_id=patient_id,
            organization_id=uuid.uuid4(),
            valid_from=NOW - DAY,
        )
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


def test_an_unknown_patient_is_rejected(scene) -> None:
    db, partner, _patient_id, _org = scene
    with pytest.raises(NotFoundError):
        consents.register_consent(
            db,
            actor=_medico(db),
            patient_id=uuid.uuid4(),
            interop_client_id=partner.client_id,
            valid_from=NOW - DAY,
        )


def test_a_window_ending_before_it_starts_is_rejected(scene) -> None:
    db, _partner, patient_id, _org = scene
    with pytest.raises(InvalidValueError):
        consents.register_consent(
            db,
            actor=_medico(db),
            patient_id=patient_id,
            interop_client_id="socio-demo",
            valid_from=NOW,
            valid_until=NOW - HOUR,
        )


@pytest.mark.parametrize(
    "username",
    ["paciente.demo", "especialista.norte", "contable.eps", "coordinador.eps"],
)
def test_only_admin_and_medico_can_register_consents(scene, username) -> None:
    db, _partner, patient_id, _org = scene
    with pytest.raises(ForbiddenError):
        consents.register_consent(
            db,
            actor=_user(db, username),
            patient_id=patient_id,
            interop_client_id="socio-demo",
            valid_from=NOW - DAY,
        )
    assert db.scalar(select(Consent.id)) is None
    assert audit_rows(db, AuditAction.CONSENT_CREATED) == []


def test_an_overlapping_live_consent_is_rejected(scene) -> None:
    db, _partner, _patient_id, _org = scene
    _register_partner(db, scene, valid_from=NOW - 10 * DAY, valid_until=None)
    with pytest.raises(ConflictError):
        _register_partner(db, scene, valid_from=NOW - DAY)
    assert len(db.scalars(select(Consent)).all()) == 1  # no second row
    assert len(audit_rows(db, AuditAction.CONSENT_CREATED)) == 1  # no extra audit


def test_an_overlap_with_a_dated_window_is_also_rejected(scene) -> None:
    db, _partner, _patient_id, _org = scene
    _register_partner(db, scene, valid_from=NOW - 10 * DAY, valid_until=NOW + 5 * DAY)
    with pytest.raises(ConflictError):
        _register_partner(
            db, scene, valid_from=NOW + 4 * DAY, valid_until=NOW + 6 * DAY
        )


def test_a_non_overlapping_window_is_a_renewal_not_a_conflict(scene) -> None:
    db, _partner, patient_id, _org = scene
    # The old consent expired two days ago; the new window starts yesterday.
    old = _register_partner(
        db, scene, valid_from=NOW - 10 * DAY, valid_until=NOW - 2 * DAY
    )
    new = _register_partner(db, scene, valid_from=NOW - DAY)
    rows = db.scalars(select(Consent)).all()
    assert {row.id for row in rows} == {old.id, new.id}
    assert old.valid_until == NOW - 2 * DAY  # history untouched: renewal is a new row


def test_a_window_after_a_live_one_is_accepted_as_a_queued_renewal(scene) -> None:
    db, _partner, _patient_id, _org = scene
    first = _register_partner(
        db, scene, valid_from=NOW - DAY, valid_until=NOW + 5 * DAY
    )
    second = _register_partner(db, scene, valid_from=NOW + 6 * DAY)
    assert second.id != first.id
    assert len(db.scalars(select(Consent)).all()) == 2


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def test_status_of_derives_activa_revocada_and_expirada() -> None:
    moment = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    active = make_consent(valid_from=moment - DAY, valid_until=None, revoked_at=None)
    assert consents.status_of(active, now=moment) == ConsentStatus.ACTIVE

    future_end = make_consent(valid_from=moment - DAY, valid_until=moment + DAY)
    assert consents.status_of(future_end, now=moment) == ConsentStatus.ACTIVE

    revoked = make_consent(
        valid_from=moment - DAY, valid_until=None, revoked_at=moment - HOUR
    )
    assert consents.status_of(revoked, now=moment) == ConsentStatus.REVOKED

    expired = make_consent(valid_from=moment - 2 * DAY, valid_until=moment - DAY)
    assert consents.status_of(expired, now=moment) == ConsentStatus.EXPIRED


def test_a_window_that_has_not_started_is_programada_not_activa() -> None:
    moment = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    future = make_consent(valid_from=moment + DAY, valid_until=None, revoked_at=None)
    assert consents.status_of(future, now=moment) == ConsentStatus.SCHEDULED
    assert ConsentStatus.SCHEDULED.value == "programada"
    # The window opening is the boundary: from that instant it is active.
    assert consents.status_of(future, now=moment + DAY) == ConsentStatus.ACTIVE
    # A revocation wins over everything, even over a window that never started.
    revoked_future = make_consent(valid_from=moment + DAY, revoked_at=moment - HOUR)
    assert consents.status_of(revoked_future, now=moment) == ConsentStatus.REVOKED


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoking_a_consent_records_the_actor_and_audits(scene) -> None:
    db, _partner, _patient_id, _org = scene
    medico = _medico(db)
    consent = _register_partner(db, scene)
    assert consent.revoked_at is None

    revoked = consents.revoke_consent(db, actor=medico, consent_id=consent.id)
    assert revoked.revoked_at is not None
    assert revoked.revoked_by == medico.id

    entries = audit_rows(db, AuditAction.CONSENT_REVOKED)
    assert len(entries) == 1
    assert entries[0].entity_id == consent.id
    assert entries[0].user_id == medico.id


def test_revoking_an_unknown_consent_raises_not_found(scene) -> None:
    db, _partner, _patient_id, _org = scene
    with pytest.raises(NotFoundError):
        consents.revoke_consent(db, actor=_medico(db), consent_id=uuid.uuid4())
    assert audit_rows(db, AuditAction.CONSENT_REVOKED) == []


def test_revoking_twice_is_a_conflict(scene) -> None:
    db, _partner, _patient_id, _org = scene
    consent = _register_partner(db, scene)
    consents.revoke_consent(db, actor=_medico(db), consent_id=consent.id)
    with pytest.raises(ConflictError):
        consents.revoke_consent(db, actor=_medico(db), consent_id=consent.id)
    assert len(audit_rows(db, AuditAction.CONSENT_REVOKED)) == 1


def test_revoking_is_restricted_to_admin_and_medico(scene) -> None:
    db, _partner, _patient_id, _org = scene
    consent = _register_partner(db, scene)
    with pytest.raises(ForbiddenError):
        consents.revoke_consent(
            db, actor=_user(db, "paciente.demo"), consent_id=consent.id
        )
    assert consent.revoked_at is None
    assert audit_rows(db, AuditAction.CONSENT_REVOKED) == []


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_consents_returns_everything_and_filters_by_patient(scene) -> None:
    db, _partner, patient_id, organization_id = scene
    medico = _medico(db)
    first = consents.register_consent(
        db,
        actor=medico,
        patient_id=patient_id,
        organization_id=organization_id,
        valid_from=NOW - DAY,
    )
    second = _register_partner(db, scene)
    other_patient = db.scalar(
        select(Patient.id).where(Patient.id != patient_id).limit(1)
    )
    assert other_patient is not None
    third = consents.register_consent(
        db,
        actor=_admin(db),
        patient_id=other_patient,
        interop_client_id="socio-demo",
        valid_from=NOW - DAY,
    )

    everything = consents.list_consents(db, _admin(db))
    assert [row.id for row in everything] == [third.id, second.id, first.id]

    filtered = consents.list_consents(db, _admin(db), paciente_id=patient_id)
    assert {row.id for row in filtered} == {first.id, second.id}


def test_list_consents_is_restricted_to_admin_and_medico(scene) -> None:
    db, _partner, _patient_id, _org = scene
    _register_partner(db, scene)
    with pytest.raises(ForbiddenError):
        consents.list_consents(db, _user(db, "paciente.demo"))


# ---------------------------------------------------------------------------
# The gate (exact E1 contract)
# ---------------------------------------------------------------------------


def test_the_gate_passes_with_a_valid_indefinite_consent(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
            valid_until=None,
        )
    )
    db.commit()
    assert (
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )
        is None
    )


def test_the_gate_passes_with_a_valid_dated_window(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
            valid_until=NOW + DAY,
        )
    )
    db.commit()
    consents.require_valid_consent(
        db, patient_id=patient_id, destination=("interop_client", "socio-demo")
    )


def test_the_gate_passes_for_an_in_network_organization(scene) -> None:
    db, _partner, patient_id, organization_id = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            organization_id=organization_id,
            interop_client_id=None,
            valid_from=NOW - DAY,
        )
    )
    db.commit()
    consents.require_valid_consent(
        db, patient_id=patient_id, destination=("organization", organization_id)
    )


def test_the_gate_blocks_when_no_consent_exists(scene) -> None:
    db, _partner, patient_id, organization_id = scene
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("organization", organization_id)
        )


def test_the_gate_blocks_an_expired_consent(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - 2 * DAY,
            valid_until=NOW - DAY,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )


def test_the_gate_blocks_a_not_yet_valid_consent(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW + DAY,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )


def test_the_gate_blocks_a_revoked_consent(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
            revoked_at=NOW - HOUR,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )


def test_a_valid_in_network_consent_does_not_open_the_partner_gate(scene) -> None:
    db, _partner, patient_id, organization_id = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            organization_id=organization_id,
            interop_client_id=None,
            valid_from=NOW - DAY,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )


def test_a_valid_partner_consent_does_not_open_the_organization_gate(scene) -> None:
    db, partner, patient_id, organization_id = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("organization", organization_id)
        )


def test_the_gate_blocks_a_wrong_patient(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
        )
    )
    db.commit()
    other_patient = db.scalar(
        select(Patient.id).where(Patient.id != patient_id).limit(1)
    )
    assert other_patient is not None
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=other_patient, destination=("interop_client", "socio-demo")
        )


def test_the_gate_blocks_an_unknown_partner_id(scene) -> None:
    db, _partner, patient_id, _org = scene
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "no-such-socio")
        )


def test_the_gate_blocks_a_revoked_dated_window_even_while_unexpired(scene) -> None:
    db, partner, patient_id, _org = scene
    db.add(
        make_consent(
            patient_id=patient_id,
            interop_client_id=partner.id,
            valid_from=NOW - DAY,
            valid_until=NOW + DAY,
            revoked_at=NOW - HOUR,
        )
    )
    db.commit()
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(
            db, patient_id=patient_id, destination=("interop_client", "socio-demo")
        )


def test_adjacent_renewal_is_allowed_and_gate_excludes_the_end(scene, monkeypatch):
    db, _partner, patient_id, _org = scene
    monkeypatch.setattr(consents, "_now", lambda: NOW)
    first = _register_partner(db, scene, valid_from=NOW - DAY, valid_until=NOW + DAY)
    second = _register_partner(db, scene, valid_from=NOW + DAY, valid_until=NOW + 2 * DAY)
    monkeypatch.setattr(consents, "_now", lambda: NOW + DAY)
    assert consents.status_of(first) == ConsentStatus.EXPIRED
    consents.require_valid_consent(db, patient_id=patient_id, destination=("interop_client", "socio-demo"))
    monkeypatch.setattr(consents, "_now", lambda: NOW + 2 * DAY)
    assert consents.status_of(second) == ConsentStatus.EXPIRED
    with pytest.raises(ForbiddenError):
        consents.require_valid_consent(db, patient_id=patient_id, destination=("interop_client", "socio-demo"))


def test_revocation_covers_future_windows_and_keeps_one_chained_audit_per_row(scene):
    db, partner, patient_id, org = scene
    first = _register_partner(db, scene, valid_from=NOW - DAY, valid_until=NOW + DAY)
    future = _register_partner(db, scene, valid_from=NOW + 2 * DAY)
    other_destination = consents.register_consent(
        db, actor=_medico(db), patient_id=patient_id, organization_id=org, valid_from=NOW - DAY,
    )
    assert audit_chain.verify(db).ok
    consents.revoke_consent(db, actor=_medico(db), consent_id=first.id)
    db.refresh(future)
    assert first.revoked_at is not None and future.revoked_at is not None
    assert other_destination.revoked_at is None
    created = audit_rows(db, AuditAction.CONSENT_CREATED)
    revoked = audit_rows(db, AuditAction.CONSENT_REVOKED)
    assert {r.entity_id for r in created} == {first.id, future.id, other_destination.id}
    assert {r.entity_id for r in revoked} == {first.id, future.id}
    assert all(r.user_id == _medico(db).id for r in created + revoked)
    assert len(revoked) == 2 and audit_chain.verify(db).ok
    with pytest.raises(ConflictError):
        consents.revoke_consent(db, actor=_medico(db), consent_id=future.id)
    assert len(audit_rows(db, AuditAction.CONSENT_REVOKED)) == 2


def test_naive_service_timestamp_is_rejected_before_comparison(scene):
    db, *_ = scene
    with pytest.raises(InvalidValueError):
        _register_partner(db, scene, valid_from=NOW.replace(tzinfo=None), valid_until=NOW + DAY)


def test_destination_lock_is_postgresql_only_and_keyed_by_patient_and_destination():
    db = Mock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    patient, destination = uuid.uuid4(), uuid.uuid4()
    consents._lock_destination(db, patient, "interop_client_id", destination)
    first = db.execute.call_args
    assert "pg_advisory_xact_lock" in str(first.args[0])
    consents._lock_destination(db, patient, "interop_client_id", destination)
    assert db.execute.call_args.args[1] == first.args[1]
    consents._lock_destination(db, uuid.uuid4(), "interop_client_id", destination)
    assert db.execute.call_args.args[1] != first.args[1]
    consents._lock_destination(db, patient, "interop_client_id", uuid.uuid4())
    assert db.execute.call_args.args[1] != first.args[1]
    db.reset_mock()
    db.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    consents._lock_destination(db, patient, "interop_client_id", destination)
    db.execute.assert_not_called()


def test_consent_list_caps_at_200_and_eagerly_loads_partners(scene):
    db, partner, patient_id, _org = scene
    db.add_all(make_consent(patient_id=patient_id, interop_client_id=partner.id) for _ in range(201))
    db.commit()
    db.expire_all()
    rows = consents.list_consents(db, _admin(db), limit=999)
    assert len(rows) == 200
    assert all("interop_client" not in inspect(row).unloaded for row in rows)
    assert all(consents.to_read(row).cliente_interop_id == "socio-demo" for row in rows)


def test_a_stale_session_cannot_revoke_twice(scene):
    from sqlalchemy.orm import Session

    db, *_ = scene
    consent = _register_partner(db, scene)
    with Session(db.get_bind(), expire_on_commit=False) as other:
        cached = other.get(Consent, consent.id)
        assert cached.revoked_at is None
        actor = _medico(other)
        consents.revoke_consent(db, actor=_medico(db), consent_id=consent.id)
        with pytest.raises(ConflictError):
            consents.revoke_consent(other, actor=actor, consent_id=cached.id)
    assert len(audit_rows(db, AuditAction.CONSENT_REVOKED)) == 1
