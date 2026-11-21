"""Patient identifiers encrypted at rest, blind-index lookups and the backfill script.

Built in SQLite in memory. The rollout is BACKWARD COMPATIBLE: with no keys the
application stores plain text exactly as before; with keys, writes are encrypted and
reads accept both the ``v1.`` format and legacy plain text, so the existing rows can
be migrated later, in batches (docs/ENTREGA_FINAL.md, D1).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import field_crypto
from app.fhir import mappers
from app.models import Base
from app.models.enums import DocumentType
from app.models.patient import Patient
from app.services import patients as patients_service
from app.services import pacs, trail
from app.services.rag.privacy import PatientIdentity
from scripts import cifrar_pacientes
from tests.factories import IDENTIFIER_SYSTEM, make_patient

DOC = "1.144.123.456"
PHONE = "3004567890"
EMAIL = "maria@example.org"
ADDRESS = "Calle 10 # 20-30"
PLAIN_VALUES = (DOC, "1144123456", PHONE, EMAIL, ADDRESS)


@pytest.fixture
def engine():  # noqa: ANN201
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(engine) -> Session:  # noqa: ANN001
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def _add(db: Session, **overrides) -> Patient:
    overrides.setdefault("document_number", DOC)
    patient = make_patient(**overrides)
    db.add(patient)
    db.commit()
    return patient


def _raw(db: Session, patient: Patient) -> dict:
    return dict(
        db.execute(
            text(
                "SELECT document_number, phone, email, address, documento_idx, updated_at "
                "FROM patients WHERE id = :id"
            ),
            {"id": patient.id.hex},
        )
        .mappings()
        .one()
    )


# -- Keys NOT configured: nothing changes -----------------------------------


def test_without_keys_the_application_stores_plain_text_as_always(db: Session) -> None:
    patient = _add(db, phone=PHONE, email=EMAIL, address=ADDRESS)
    raw = _raw(db, patient)
    assert (raw["document_number"], raw["phone"], raw["email"], raw["address"]) == (
        DOC, PHONE, EMAIL, ADDRESS,
    )
    assert raw["documento_idx"] is None
    assert patient.document_number == DOC and patient.phone == PHONE


def test_without_keys_lookups_compare_the_stored_text(db: Session) -> None:
    patient = _add(db)
    found = db.scalar(select(Patient).where(Patient.document_matches(DOC)))
    assert found is not None and found.id == patient.id
    assert db.scalar(select(Patient).where(Patient.document_matches("1144-123-456"))) is None


def test_the_class_level_attributes_still_work_in_sql(db: Session) -> None:
    _add(db)
    assert db.scalars(select(Patient.document_number)).all() == [DOC]
    assert db.scalar(select(Patient).where(Patient.document_number == DOC)) is not None


# -- Keys configured ---------------------------------------------------------


def test_with_keys_the_four_identifiers_are_stored_encrypted(db: Session, field_keys) -> None:
    patient = _add(db, phone=PHONE, email=EMAIL, address=ADDRESS)
    raw = _raw(db, patient)
    for column in ("document_number", "phone", "email", "address"):
        assert raw[column].startswith("v1.k1."), column
    assert not any(value in str(raw) for value in PLAIN_VALUES)
    assert raw["documento_idx"] == field_crypto.blind_index(DOC)

    db.refresh(patient)
    assert (patient.document_number, patient.phone, patient.email, patient.address) == (
        DOC, PHONE, EMAIL, ADDRESS,
    )


def test_the_id_exists_before_the_first_flush_because_the_aad_needs_it(field_keys) -> None:
    patient = Patient(
        document_type=DocumentType.CC,
        document_number=DOC,
        first_name="Ana",
        last_name="Gomez",
        birth_date=make_patient().birth_date,
        gender=make_patient().gender,
        phone=PHONE,
        eps_organization_id=uuid.uuid4(),
    )
    assert patient.id is not None
    stored = patient._phone
    assert field_crypto.decrypt_field(stored, field_crypto.aad(patient.id, "telefono")) == PHONE


def test_an_explicit_id_is_honoured_whatever_the_argument_order(field_keys) -> None:
    fixed = uuid.uuid4()
    patient = make_patient(phone=PHONE)
    patient2 = Patient(
        phone=PHONE,
        id=fixed,
        document_type=DocumentType.CC,
        document_number=DOC,
        first_name="Ana",
        last_name="Gomez",
        birth_date=patient.birth_date,
        gender=patient.gender,
        eps_organization_id=uuid.uuid4(),
    )
    assert patient2.id == fixed
    assert patient2.phone == PHONE  # decrypts with the AAD of ``fixed``


def test_editing_and_clearing_an_identifier_reencrypts_it(db: Session, field_keys) -> None:
    patient = _add(db, phone=PHONE)
    before = _raw(db, patient)["phone"]

    patient.phone = "3119999999"
    db.commit()
    after = _raw(db, patient)["phone"]
    assert after.startswith("v1.k1.") and after != before
    assert patient.phone == "3119999999"

    patient.phone = None
    db.commit()
    assert _raw(db, patient)["phone"] is None
    assert patient.phone is None


def test_a_document_is_found_whatever_its_format(db: Session, field_keys) -> None:
    patient = _add(db)  # stored as "1.144.123.456"
    for typed in ("1144-123-456", "1.144.123.456", "1144 123 456", "1144123456"):
        found = db.scalar(select(Patient).where(Patient.document_matches(typed)))
        assert found is not None and found.id == patient.id, typed
    assert db.scalar(select(Patient).where(Patient.document_matches("1144123457"))) is None


def test_the_listing_service_searches_by_blind_index(db: Session, field_keys) -> None:
    patient = _add(db)
    items, total = patients_service.list_patients(
        db, page=1, page_size=10, document_number="1144-123-456"
    )
    assert total == 1 and items[0].id == patient.id


def test_a_duplicate_document_is_detected_in_any_format(db: Session, field_keys) -> None:
    _add(db)
    assert patients_service._exists_with_document(db, DocumentType.CC, "1144-123-456")
    assert not patients_service._exists_with_document(db, DocumentType.TI, "1144-123-456")
    assert not patients_service._exists_with_document(db, DocumentType.CC, "999999")


def test_the_database_refuses_two_patients_with_the_same_indexed_document(
    db: Session, field_keys
) -> None:
    _add(db)
    db.add(make_patient(document_number="1144-123-456"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_legacy_plain_text_row_stays_readable_and_findable_once_keys_exist(
    db: Session, request
) -> None:
    legacy = _add(db, document_number="1032456789", phone=PHONE)  # written with NO keys
    assert _raw(db, legacy)["documento_idx"] is None

    request.getfixturevalue("field_keys")  # now the keys are configured
    db.expire_all()
    reloaded = db.get(Patient, legacy.id)
    assert reloaded.document_number == "1032456789" and reloaded.phone == PHONE
    # Rows without an index are found by the legacy equality on the stored text...
    found = db.scalar(select(Patient).where(Patient.document_matches("1032456789")))
    assert found is not None and found.id == legacy.id
    # ...which is exact: the dotted form only works once the row is backfilled.
    assert db.scalar(select(Patient).where(Patient.document_matches("1.032.456.789"))) is None


def test_old_and_new_rows_coexist(db: Session, request) -> None:
    legacy = _add(db, document_number="1032456789")
    request.getfixturevalue("field_keys")
    fresh = _add(db, document_number="2000111222")
    assert _raw(db, legacy)["document_number"] == "1032456789"
    assert _raw(db, fresh)["document_number"].startswith("v1.")
    for number, expected in (("1032456789", legacy), ("2000-111-222", fresh)):
        found = db.scalar(select(Patient).where(Patient.document_matches(number)))
        assert found is not None and found.id == expected.id


def test_a_ciphertext_copied_to_another_patient_does_not_decrypt(db: Session, field_keys) -> None:
    mine = _add(db, document_number="1111111111", phone=PHONE)
    other = _add(db, document_number="2222222222", phone="3200000000")
    db.execute(
        text("UPDATE patients SET phone = :phone WHERE id = :id"),
        {"phone": _raw(db, mine)["phone"], "id": other.id.hex},
    )
    db.commit()
    db.refresh(other)
    # Fail soft (finding 5): the cell reads as a placeholder instead of breaking the list.
    assert other.phone == field_crypto.UNAVAILABLE
    assert other.document_number == "2222222222"
    with pytest.raises(field_crypto.FieldDecryptionError):
        field_crypto.reveal(other._phone, other.id, "telefono", strict=True)


@pytest.mark.parametrize("field", ["document_number", "phone", "email", "address"])
def test_the_schemas_reject_plaintext_shaped_like_a_ciphertext(field) -> None:
    from pydantic import ValidationError

    from app.schemas.patient import PatientCreate, PatientUpdate

    poison = "v1.k1.AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBBBB"
    with pytest.raises(ValidationError):
        PatientUpdate(**{field: poison})
    base = {
        "document_type": "CC", "document_number": "1032456789", "first_name": "Ana",
        "last_name": "Perez", "birth_date": "1990-01-01", "gender": "female",
        "eps_organization_id": str(uuid.uuid4()),
    }
    PatientCreate(**base)  # the baseline is valid
    with pytest.raises(ValidationError):
        PatientCreate(**{**base, field: poison})


@pytest.mark.parametrize("field", ["document_number", "phone", "email", "address"])
def test_the_model_refuses_plaintext_shaped_like_a_ciphertext(field_keys, field) -> None:
    patient = make_patient(document_number="1032456789")
    with pytest.raises(ValueError):
        setattr(patient, field, "v1.k1.AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBBBB")


def test_history_snapshots_hold_the_stored_values_never_a_plain_copy(db: Session, field_keys) -> None:
    patient = _add(db, phone=PHONE)
    snapshot = trail.snapshot_row(patient)
    assert snapshot["phone"].startswith("v1.k1.")
    assert snapshot["document_number"].startswith("v1.k1.")
    assert snapshot["documento_idx"] == field_crypto.blind_index(DOC)
    assert "_phone" not in snapshot
    assert not any(value in str(snapshot) for value in PLAIN_VALUES)


def test_every_reader_gets_plain_text_through_the_properties(field_keys) -> None:
    patient = make_patient(document_number="1032456789", phone=PHONE, email=EMAIL, address=ADDRESS)
    assert patient._phone.startswith("v1.k1.")

    resource = mappers.patient_to_fhir(patient, identifier_system=IDENTIFIER_SYSTEM)
    identifiers = {i["system"]: i["value"] for i in resource["identifier"]}
    assert identifiers[f"{IDENTIFIER_SYSTEM}:documento"] == "CC-1032456789"
    assert identifiers[f"{IDENTIFIER_SYSTEM}:numero"] == "1032456789"
    assert {"system": "phone", "value": PHONE} in resource["telecom"]
    assert resource["address"] == [{"text": ADDRESS}]

    identity = PatientIdentity.of(patient, "PAC-1")
    assert (identity.document_number, identity.phone, identity.email, identity.address) == (
        "1032456789", PHONE, EMAIL, ADDRESS,
    )

    study = SimpleNamespace(study_instance_uid="1.2.3", accession_number="A1", description="x", body_site="CHEST")
    assert pacs._dicom_tags(study, patient)["PatientID"] == "1032456789"


# -- Backfill script ---------------------------------------------------------


def _factory(engine):  # noqa: ANN001, ANN202
    return lambda: sessionmaker(bind=engine, expire_on_commit=False)()


def _legacy_rows(db: Session) -> list[Patient]:
    return [
        _add(db, document_number="1032456789", phone=PHONE, email=EMAIL, address=ADDRESS),
        _add(db, document_number="2000111222", phone=None, email=None, address=None),
        _add(db, document_number="3000999888", phone="3200000000", email=None, address=ADDRESS),
    ]


def test_the_backfill_dry_run_only_counts_and_never_prints_a_value(
    db: Session, engine, request, capsys
) -> None:
    rows = _legacy_rows(db)
    before = [_raw(db, p) for p in rows]
    request.getfixturevalue("field_keys")

    assert cifrar_pacientes.main([], session_factory=_factory(engine)) == 0

    out = capsys.readouterr().out
    assert "documento_sin_cifrar" in out and "Modo prueba" in out
    assert not any(v in out for v in ("1032456789", "2000111222", PHONE, EMAIL, "Calle"))
    assert [_raw(db, p) for p in rows] == before


def test_apply_requires_the_keys(db: Session, engine) -> None:
    _legacy_rows(db)
    assert cifrar_pacientes.main(["--apply"], session_factory=_factory(engine)) == 2
    assert _raw(db, db.scalars(select(Patient)).first())["document_number"][:3] != "v1."


def test_the_backfill_encrypts_everything_keeps_updated_at_and_is_idempotent(
    db: Session, engine, request, capsys
) -> None:
    rows = _legacy_rows(db)
    before = [_raw(db, p) for p in rows]
    request.getfixturevalue("field_keys")

    assert cifrar_pacientes.main(["--apply", "--batch-size", "2"], session_factory=_factory(engine)) == 0
    out = capsys.readouterr().out
    assert "Filas modificadas: 3" in out
    assert not any(v in out for v in ("1032456789", "2000111222", PHONE, EMAIL))

    db.expire_all()
    for patient, old in zip(rows, before):
        raw = _raw(db, patient)
        assert raw["document_number"].startswith("v1.k1.")
        assert raw["documento_idx"] == field_crypto.blind_index(old["document_number"])
        assert raw["updated_at"] == old["updated_at"]
        for column in ("phone", "email", "address"):
            if old[column] is None:
                assert raw[column] is None
            else:
                assert raw[column].startswith("v1.k1.")
        reloaded = db.get(Patient, patient.id)
        assert reloaded.document_number == old["document_number"]
        assert reloaded.phone == old["phone"]

    # Second run: nothing left to do, nothing rewritten.
    snapshot = [_raw(db, p) for p in rows]
    assert cifrar_pacientes.main(["--apply"], session_factory=_factory(engine)) == 0
    assert "Filas modificadas: 0" in capsys.readouterr().out
    assert [_raw(db, p) for p in rows] == snapshot
    # And the migrated rows are now found through the blind index in any format.
    assert db.scalar(select(Patient).where(Patient.document_matches("1.032.456.789"))) is not None


def test_the_backfill_skips_values_that_are_already_encrypted(
    db: Session, engine, request, capsys
) -> None:
    legacy = _add(db, document_number="1032456789", phone=PHONE)
    request.getfixturevalue("field_keys")
    sealed = field_crypto.seal(PHONE, legacy.id, "telefono")
    db.execute(text("UPDATE patients SET phone = :p WHERE id = :id"), {"p": sealed, "id": legacy.id.hex})
    db.commit()

    assert cifrar_pacientes.main(["--apply"], session_factory=_factory(engine)) == 0
    assert _raw(db, legacy)["phone"] == sealed  # byte-identical: not re-encrypted


def test_the_backfill_refuses_documents_that_would_collide(
    db: Session, engine, request, capsys
) -> None:
    first = _add(db, document_number="1.144.123.456")
    second = _add(db, document_number="1144123456")
    request.getfixturevalue("field_keys")

    assert cifrar_pacientes.main(["--apply"], session_factory=_factory(engine)) == 1
    assert "ABORTADO" in capsys.readouterr().out
    assert _raw(db, first)["document_number"] == "1.144.123.456"
    assert _raw(db, second)["document_number"] == "1144123456"
