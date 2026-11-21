"""AES-256-GCM field encryption and the blind index (app/core/field_crypto.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

import pytest
from pydantic import ValidationError

from app.core import field_crypto
from app.core.config import Settings
from app.core.field_crypto import (
    FieldCryptoConfigError,
    FieldCryptoError,
    FieldDecryptionError,
)
from tests.conftest import TEST_BLIND_KEY, TEST_KEY_1, TEST_KEY_2

PATIENT = uuid.UUID("00000000-0000-0000-0000-000000000017")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000018")


def _aad(patient: uuid.UUID = PATIENT, field: str = "documento") -> bytes:
    return field_crypto.aad(patient, field)


def test_the_associated_data_names_the_patient_and_the_field() -> None:
    assert _aad() == b"paciente:00000000-0000-0000-0000-000000000017|documento"
    assert _aad(field="telefono").endswith(b"|telefono")
    with pytest.raises(FieldCryptoError):
        field_crypto.aad(PATIENT, "nombre")  # clinical/other data is not encrypted


def test_the_stored_format_is_version_kid_nonce_ciphertext(field_keys) -> None:
    stored = field_crypto.encrypt_field("1144123456", _aad())
    version, kid, nonce, ciphertext = stored.split(".")
    assert (version, kid) == ("v1", "k1")
    padded = nonce + "=" * (-len(nonce) % 4)
    assert len(base64.urlsafe_b64decode(padded)) == field_crypto.NONCE_BYTES == 12
    assert ciphertext and "1144123456" not in stored
    assert field_crypto.is_encrypted(stored)
    assert not field_crypto.is_encrypted("1144123456")


def test_encrypting_twice_gives_two_different_values(field_keys) -> None:
    first = field_crypto.encrypt_field("1144123456", _aad())
    second = field_crypto.encrypt_field("1144123456", _aad())
    assert first != second
    assert field_crypto.decrypt_field(first, _aad()) == field_crypto.decrypt_field(second, _aad())


def test_roundtrip_keeps_accents_and_symbols(field_keys) -> None:
    text = "Calle 10 # 20-30, Apto 4B - Medellín ñandú"
    stored = field_crypto.encrypt_field(text, _aad(field="direccion"))
    assert field_crypto.decrypt_field(stored, _aad(field="direccion")) == text


def test_decrypting_as_another_patient_fails(field_keys) -> None:
    stored = field_crypto.encrypt_field("1144123456", _aad(PATIENT))
    with pytest.raises(FieldDecryptionError):
        field_crypto.decrypt_field(stored, _aad(OTHER))


def test_decrypting_as_another_field_fails(field_keys) -> None:
    stored = field_crypto.encrypt_field("3004567890", _aad(field="telefono"))
    with pytest.raises(FieldDecryptionError):
        field_crypto.decrypt_field(stored, _aad(field="correo"))


def test_altering_one_character_fails(field_keys) -> None:
    version, kid, nonce, ciphertext = field_crypto.encrypt_field("1144123456", _aad()).split(".")
    middle = len(ciphertext) // 2
    flipped = "A" if ciphertext[middle] != "A" else "B"
    tampered = ".".join([version, kid, nonce, ciphertext[:middle] + flipped + ciphertext[middle + 1:]])
    with pytest.raises(FieldDecryptionError):
        field_crypto.decrypt_field(tampered, _aad())


def test_a_malformed_value_fails_cleanly(field_keys) -> None:
    for broken in ("v1.k1.solo-tres", "v2.k1.abc.def", "texto plano", "v1.k1.!!.??"):
        with pytest.raises(FieldDecryptionError):
            field_crypto.decrypt_field(broken, _aad())


def test_errors_never_carry_the_value(field_keys) -> None:
    stored = field_crypto.encrypt_field("1144123456", _aad())
    with pytest.raises(FieldDecryptionError) as error:
        field_crypto.decrypt_field(stored, _aad(OTHER))
    assert "1144123456" not in str(error.value) and stored not in str(error.value)


def test_kid_rotation_decrypts_old_values_and_writes_with_the_active_key(
    field_keys, monkeypatch
) -> None:
    old = field_crypto.encrypt_field("1144123456", _aad())
    assert old.split(".")[1] == "k1"

    monkeypatch.setattr(field_keys, "field_encryption_active_kid", "k2")
    new = field_crypto.encrypt_field("1144123456", _aad())
    assert new.split(".")[1] == "k2"
    assert field_crypto.decrypt_field(old, _aad()) == "1144123456"
    assert field_crypto.decrypt_field(new, _aad()) == "1144123456"


def test_a_removed_key_cannot_decrypt_what_it_sealed(field_keys, monkeypatch) -> None:
    old = field_crypto.encrypt_field("1144123456", _aad())
    monkeypatch.setattr(field_keys, "field_encryption_keys", f"k2:{TEST_KEY_2}")
    monkeypatch.setattr(field_keys, "field_encryption_active_kid", "k2")
    with pytest.raises(FieldDecryptionError):
        field_crypto.decrypt_field(old, _aad())


def test_seal_and_reveal_pass_plain_text_through_without_keys() -> None:
    assert not field_crypto.encryption_enabled()
    assert field_crypto.seal("3004567890", PATIENT, "telefono") == "3004567890"
    assert field_crypto.seal(None, PATIENT, "telefono") is None
    assert field_crypto.reveal("3004567890", PATIENT, "telefono") == "3004567890"


def test_with_keys_seal_encrypts_and_reveal_reads_both_formats(field_keys) -> None:
    sealed = field_crypto.seal("3004567890", PATIENT, "telefono")
    assert sealed.startswith("v1.k1.")
    assert field_crypto.reveal(sealed, PATIENT, "telefono") == "3004567890"
    # A legacy plain-text row is still readable once keys are configured.
    assert field_crypto.reveal("3004567890", PATIENT, "telefono") == "3004567890"
    assert field_crypto.seal("", PATIENT, "telefono") == ""


def test_encrypted_data_without_keys_is_an_error_not_garbage(field_keys, monkeypatch) -> None:
    sealed = field_crypto.seal("3004567890", PATIENT, "telefono")
    monkeypatch.setattr(field_keys, "field_encryption_keys", "")
    with pytest.raises(FieldCryptoConfigError):
        field_crypto.reveal(sealed, PATIENT, "telefono", strict=True)


def test_reveal_fails_soft_by_default_and_logs_the_patient(field_keys, monkeypatch, caplog) -> None:
    sealed = field_crypto.seal("3004567890", PATIENT, "telefono")
    monkeypatch.setattr(field_keys, "field_encryption_keys", "")
    with caplog.at_level("WARNING", logger="app.core.field_crypto"):
        assert field_crypto.reveal(sealed, PATIENT, "telefono") == field_crypto.UNAVAILABLE
    assert any(str(PATIENT) in r.getMessage() and "telefono" in r.getMessage() for r in caplog.records)
    assert not any("3004567890" in r.getMessage() for r in caplog.records)


def test_a_value_shaped_like_a_ciphertext_is_refused_as_plaintext(field_keys, monkeypatch) -> None:
    poison = "v1.k1.AAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBBBB"
    with pytest.raises(ValueError):
        field_crypto.seal(poison, PATIENT, "documento")
    monkeypatch.setattr(field_keys, "field_encryption_keys", "")  # encryption off: still refused
    with pytest.raises(ValueError):
        field_crypto.seal(poison, PATIENT, "documento")


# -- Configuration ----------------------------------------------------------


def test_the_key_setup_is_validated() -> None:
    validate = field_crypto.validate_key_material
    validate(None, None, None)  # nothing configured is valid: encryption is off
    validate(f"k1:{TEST_KEY_1}", "k1", TEST_BLIND_KEY)
    validate(f"k1:{TEST_KEY_1},k2:{TEST_KEY_2}", "k2", TEST_BLIND_KEY)

    short = base64.b64encode(b"short").decode()
    cases = [
        (f"k1:{short}", "k1", None),  # not 32 bytes
        (f"k1:{TEST_KEY_1}", None, None),  # keys without an active kid
        (f"k1:{TEST_KEY_1}", "k9", None),  # active kid not in the list
        (None, "k1", None),  # active kid without keys
        ("sin-forma", "k1", None),  # not kid:base64
        (f"k1:{TEST_KEY_1},k1:{TEST_KEY_2}", "k1", None),  # repeated kid
        (f"k.1:{TEST_KEY_1}", "k.1", None),  # a dot would break the stored format
        (f"k1:{TEST_KEY_1}", "k1", "corta"),  # blind key too short
        (f"k1:{TEST_KEY_1}", "k1", None),  # keys without a blind index key (finding 12)
        (f"k1:{TEST_KEY_1}", "k1", "   "),  # a blank blind key counts as missing
    ]
    for keys, kid, blind in cases:
        with pytest.raises(FieldCryptoConfigError):
            validate(keys, kid, blind)


def test_the_blind_index_key_must_differ_from_the_encryption_keys() -> None:
    validate = field_crypto.validate_key_material
    with pytest.raises(FieldCryptoConfigError):
        validate(f"k1:{TEST_KEY_1}", "k1", TEST_KEY_1)  # the same text
    with pytest.raises(FieldCryptoConfigError):
        validate(f"k1:{TEST_KEY_1},k2:{TEST_KEY_2}", "k1", TEST_KEY_2)  # any of them
    validate(f"k1:{TEST_KEY_1}", "k1", TEST_KEY_2)  # a different key is fine


def test_a_bad_setup_stops_the_settings_at_start_up(monkeypatch) -> None:
    monkeypatch.setenv("FIELD_ENCRYPTION_KEYS", f"k1:{TEST_KEY_1}")
    monkeypatch.setenv("FIELD_ENCRYPTION_ACTIVE_KID", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("FIELD_ENCRYPTION_ACTIVE_KID", "k1")
    monkeypatch.setenv("BLIND_INDEX_KEY", TEST_KEY_1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("BLIND_INDEX_KEY", TEST_BLIND_KEY)
    assert Settings(_env_file=None).field_encryption_active_kid == "k1"


def test_unset_keys_mean_the_feature_is_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.field_encryption_keys in (None, "")
    assert field_crypto.current_keyring() is None
    assert field_crypto.blind_index("1144123456") is None


# -- Blind index ------------------------------------------------------------


def test_the_document_is_normalized_before_indexing() -> None:
    normalize = field_crypto.normalize_document
    assert normalize("1.144.123.456") == normalize("1144-123-456") == normalize("1144 123 456")
    assert normalize("1144123456") == "1144123456"
    assert normalize("ab-123.x") == "AB123X"  # passports: upper case


def test_the_blind_index_is_hmac_sha256_of_the_normalized_document(field_keys) -> None:
    expected = hmac.new(
        TEST_BLIND_KEY.encode(), b"1144123456", hashlib.sha256
    ).hexdigest()
    assert field_crypto.blind_index("1.144.123.456") == expected
    assert len(expected) == 64


def test_formats_of_the_same_document_find_each_other(field_keys) -> None:
    assert field_crypto.blind_index("1144-123-456") == field_crypto.blind_index("1.144.123.456")
    assert field_crypto.blind_index("1144123457") != field_crypto.blind_index("1144123456")


def test_the_index_depends_on_its_own_key(field_keys, monkeypatch) -> None:
    first = field_crypto.blind_index("1144123456")
    monkeypatch.setattr(field_keys, "blind_index_key", "another-blind-index-key-not-real")
    assert field_crypto.blind_index("1144123456") != first


def test_the_encryption_key_is_not_a_valid_index_key(field_keys) -> None:
    index = field_crypto.blind_index("1144123456")
    as_encryption_key = hmac.new(base64.b64decode(TEST_KEY_1), b"1144123456", hashlib.sha256)
    assert index != as_encryption_key.hexdigest()


def test_a_blank_document_has_no_index(field_keys) -> None:
    assert field_crypto.blind_index(None) is None
    assert field_crypto.blind_index("   ") is None
