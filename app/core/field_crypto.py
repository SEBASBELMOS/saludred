"""Field-level encryption (AES-256-GCM) and the blind index of the patient document.

What is encrypted: the direct identifiers of a patient (document, phone, email,
address). Clinical data stays in clear: the models use it and it is already
separated from the identity (R12).

Stored format, one string per value::

    v1.<kid>.<nonce base64url>.<ciphertext+tag base64url>

* ``v1`` is the format version; ``kid`` names the key that sealed the value, so a
  key can be rotated without losing what was written with the old one: every key
  of ``FIELD_ENCRYPTION_KEYS`` decrypts, only ``FIELD_ENCRYPTION_ACTIVE_KID`` writes.
* The nonce is 12 random bytes per value: encrypting the same text twice gives two
  different strings, so the column leaks neither equality nor frequency.
* The AAD binds the ciphertext to its owner and field (``paciente:<id>|documento``).
  Copying a value to another patient, or to another column, fails to decrypt: GCM
  authenticates the AAD. Altering one character fails too.

Searching an encrypted document is done with a blind index: ``HMAC-SHA256`` of the
normalized document under a key that is NOT any encryption key. It is
deterministic (the same document always gives the same value, so ``WHERE
documento_idx = ?`` works) and cannot be reversed without the key.

Rollout rule (backward compatible): with no keys configured nothing is encrypted and
nothing changes. With keys, writes are encrypted and reads accept both the ``v1.``
format and legacy plain text, so rows can be migrated later, in batches.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

logger = logging.getLogger(__name__)

VERSION = "v1"
KEY_BYTES = 32
NONCE_BYTES = 12
MIN_BLIND_KEY_CHARS = 16

# The fields that are encrypted; each one is part of the AAD.
FIELDS = ("documento", "telefono", "correo", "direccion")

_KID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_STORED = re.compile(r"^v1\.([A-Za-z0-9_-]{1,16})\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)$")


class FieldCryptoError(Exception):
    """Base class of the errors of this module. Messages never carry a value."""


class FieldCryptoConfigError(FieldCryptoError, ValueError):
    """The keys are missing or malformed (a ``ValueError`` so pydantic reports it)."""


class FieldDecryptionError(FieldCryptoError):
    """The value does not decrypt: wrong owner/field, altered, or unknown key."""


@dataclass(frozen=True)
class Keyring:
    keys: dict[str, bytes]
    active_kid: str


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------


def _b64decode(text: str) -> bytes:
    cleaned = text.strip().replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        raise FieldCryptoConfigError("una llave no es base64 valido") from None


def parse_keys(raw: str | None) -> dict[str, bytes]:
    """``k1:<base64>,k2:<base64>`` -> ``{"k1": 32 bytes, ...}``."""

    keys: dict[str, bytes] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        kid, separator, material = item.partition(":")
        if not separator or not _KID.match(kid.strip()):
            raise FieldCryptoConfigError(
                "FIELD_ENCRYPTION_KEYS debe tener la forma kid:base64,kid:base64 "
                "(kid de 1 a 16 letras, digitos, _ o -)"
            )
        key = _b64decode(material)
        if len(key) != KEY_BYTES:
            raise FieldCryptoConfigError(
                f"la llave {kid.strip()} debe tener {KEY_BYTES} bytes (AES-256)"
            )
        if kid.strip() in keys:
            raise FieldCryptoConfigError(f"el kid {kid.strip()} esta repetido")
        keys[kid.strip()] = key
    return keys


def validate_key_material(
    keys: str | None, active_kid: str | None, blind_key: str | None
) -> None:
    """Raise ``FieldCryptoConfigError`` on any inconsistent combination.

    Called when the settings are built, so a bad configuration stops the start-up
    instead of failing on the first patient written.
    """

    keys = (keys or "").strip() or None
    active_kid = (active_kid or "").strip() or None
    blind_key = (blind_key or "").strip() or None

    parsed = parse_keys(keys)
    if parsed:
        if active_kid is None:
            raise FieldCryptoConfigError(
                "FIELD_ENCRYPTION_ACTIVE_KID es obligatorio si hay FIELD_ENCRYPTION_KEYS"
            )
        if active_kid not in parsed:
            raise FieldCryptoConfigError(
                "FIELD_ENCRYPTION_ACTIVE_KID no esta entre las llaves de FIELD_ENCRYPTION_KEYS"
            )
        # Encrypted documents without a blind index cannot be searched, and the
        # duplicate-document guard stops working: refuse to start half-configured.
        if blind_key is None:
            raise FieldCryptoConfigError(
                "BLIND_INDEX_KEY es obligatorio si hay FIELD_ENCRYPTION_KEYS"
            )
    elif active_kid is not None:
        raise FieldCryptoConfigError(
            "FIELD_ENCRYPTION_ACTIVE_KID sin FIELD_ENCRYPTION_KEYS"
        )

    if blind_key is not None:
        if len(blind_key) < MIN_BLIND_KEY_CHARS:
            raise FieldCryptoConfigError(
                f"BLIND_INDEX_KEY debe tener al menos {MIN_BLIND_KEY_CHARS} caracteres"
            )
        # The blind index and the encryption must never share a key: the index is
        # published as a column, and a shared key would tie it to the ciphertext.
        blind_bytes = blind_key.encode("utf-8")
        candidates = {blind_bytes}
        try:
            candidates.add(_b64decode(blind_key))
        except FieldCryptoConfigError:
            pass
        for material in (item.partition(":")[2].strip() for item in (keys or "").split(",")):
            if material and material.encode("utf-8") in candidates:
                raise FieldCryptoConfigError(
                    "BLIND_INDEX_KEY debe ser distinta de las llaves de cifrado"
                )
        if any(key in candidates for key in parsed.values()):
            raise FieldCryptoConfigError(
                "BLIND_INDEX_KEY debe ser distinta de las llaves de cifrado"
            )


@lru_cache(maxsize=8)
def _keyring(keys: str, active_kid: str) -> Keyring:
    return Keyring(keys=parse_keys(keys), active_kid=active_kid)


def current_keyring() -> Keyring | None:
    """The configured keyring, or ``None`` when encryption is not enabled."""

    settings = get_settings()
    keys = (settings.field_encryption_keys or "").strip()
    if not keys:
        return None
    kid = (settings.field_encryption_active_kid or "").strip()
    keyring = _keyring(keys, kid)
    if kid not in keyring.keys:
        raise FieldCryptoConfigError("FIELD_ENCRYPTION_ACTIVE_KID no esta entre las llaves")
    return keyring


def encryption_enabled() -> bool:
    return current_keyring() is not None


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def aad(patient_id: uuid.UUID | str, field: str) -> bytes:
    """Associated data: who owns the value and which field it is."""

    if field not in FIELDS:
        raise FieldCryptoError(f"campo desconocido: {field}")
    return f"paciente:{patient_id}|{field}".encode("utf-8")


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and _STORED.match(value) is not None


def encrypt_field(
    plaintext: str, associated_data: bytes, *, keyring: Keyring | None = None
) -> str:
    keyring = keyring or current_keyring()
    if keyring is None:
        raise FieldCryptoConfigError("el cifrado de campos no esta configurado")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(keyring.keys[keyring.active_kid]).encrypt(
        nonce, plaintext.encode("utf-8"), associated_data
    )
    return f"{VERSION}.{keyring.active_kid}.{_b64url(nonce)}.{_b64url(ciphertext)}"


def decrypt_field(
    stored: str, associated_data: bytes, *, keyring: Keyring | None = None
) -> str:
    match = _STORED.match(stored)
    if match is None:
        raise FieldDecryptionError("el valor no tiene el formato v1.kid.nonce.cifrado")
    keyring = keyring or current_keyring()
    if keyring is None:
        raise FieldCryptoConfigError(
            "hay datos cifrados y FIELD_ENCRYPTION_KEYS no esta configurado"
        )
    kid, nonce_text, ciphertext_text = match.groups()
    key = keyring.keys.get(kid)
    if key is None:
        raise FieldDecryptionError(f"la llave {kid} no esta configurada")
    try:
        return AESGCM(key).decrypt(
            _unb64url(nonce_text), _unb64url(ciphertext_text), associated_data
        ).decode("utf-8")
    except (InvalidTag, ValueError, binascii.Error):
        raise FieldDecryptionError(
            "no se pudo descifrar: valor alterado, de otro paciente o de otro campo"
        ) from None


class StoredShapeInputError(FieldCryptoError, ValueError):
    """A plaintext input that looks like a stored ciphertext (``v1.<kid>.<n>.<c>``)."""


UNAVAILABLE = "[no disponible]"


def reject_stored_shape(value: object) -> None:
    """Refuse plaintext shaped like the storage format.

    Such a value would be stored as legacy "plain text" and then be treated as a
    ciphertext on every read, which would fail for the whole patient (list pages
    included). Raised as a ``ValueError`` so pydantic reports it as a 422.
    """

    if is_encrypted(value):
        raise StoredShapeInputError(
            "el valor no puede tener la forma interna del cifrado (v1.<kid>.<nonce>.<cifrado>)"
        )


def seal(value: str | None, patient_id: uuid.UUID | str, field: str) -> str | None:
    """What to STORE for ``value``: encrypted when the keys exist, else unchanged."""

    if value is None or value == "":
        return value
    reject_stored_shape(value)
    if not encryption_enabled():
        return value
    return encrypt_field(value, aad(patient_id, field))


def reveal(
    stored: str | None, patient_id: uuid.UUID | str, field: str, *, strict: bool = False
) -> str | None:
    """The plain text of a stored value. Legacy plain text passes through.

    By default a value that cannot be decrypted (missing key, altered, copied from
    another patient) does NOT raise: one bad cell must not take down a whole list or
    serialization. The patient id and field are logged at WARNING (never the value) and
    ``UNAVAILABLE`` is returned. ``strict=True`` raises, for the backfill verification.
    """

    if stored is None or not is_encrypted(stored):
        return stored
    if strict:
        return decrypt_field(stored, aad(patient_id, field))
    try:
        return decrypt_field(stored, aad(patient_id, field))
    except FieldCryptoError as exc:
        logger.warning(
            "No se pudo descifrar paciente=%s campo=%s (%s)", patient_id, field, type(exc).__name__
        )
        return UNAVAILABLE


# ---------------------------------------------------------------------------
# Blind index
# ---------------------------------------------------------------------------


def normalize_document(document: str) -> str:
    """``1.144.123-456`` and ``1144 123 456`` are the same document.

    The slide removes the dots; its notes expect the hyphen to be ignored too, and
    letters (passports) are compared in upper case.
    """

    text = unicodedata.normalize("NFKC", document)
    return re.sub(r"[.\-\s]", "", text).upper()


def blind_index(document: str | None) -> str | None:
    """``HMAC-SHA256(BLIND_INDEX_KEY, normalized document)`` in hex, or ``None``.

    ``None`` when the key is not configured: lookups then fall back to the legacy
    plain-text comparison.
    """

    key = (get_settings().blind_index_key or "").strip()
    if not key or not document or not document.strip():
        return None
    return hmac.new(
        key.encode("utf-8"), normalize_document(document).encode("utf-8"), hashlib.sha256
    ).hexdigest()
