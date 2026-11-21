"""Federated patient identifier (slides 25-26).

Patient/17 exists only in one team's database, and sending the document puts a direct
identifier in every message. Instead, every team of the exchange derives the same opaque
value from the same inputs::

    mensaje = "<TIPO>|<numero normalizado>"
    id_federado = HMAC-SHA256(FEDERATED_SECRET, mensaje).hexdigest()[:32]

The type is upper-cased and must be CC, TI, CE or PA; the number is NFKC-normalized,
trimmed and stripped of ``.``, ``-`` and spaces, exactly as the JavaScript of slide 26
does, so ``1.144.123.456`` and ``1144123456`` give the same id.

Limit (slide 26 notes): whoever holds the secret and a list of documents can recompute
the ids. The secret is kept like any other and rotated if it leaks.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from typing import Any

from app.core.config import get_settings

TYPES = ("CC", "TI", "CE", "PA")
ID_LENGTH = 32
_SEPARATORS = re.compile(r"[. \-]")
_SHAPE = re.compile(r"^[0-9a-f]{32}$")

# Upper bound of the candidate patients examined by the fallback (see
# ``patient_by_federated_id``). The dataset is ~400 patients; this only guards against
# an accidental full scan of a very large table.
FALLBACK_MAX_CANDIDATES = 5000


class FederatedIdError(ValueError):
    """The type or the number cannot produce a federated id."""


class FederatedDisabledError(RuntimeError):
    """``FEDERATED_SECRET`` is not configured: the federated id is switched off."""


def enabled() -> bool:
    return get_settings().federated_secret_value is not None


def looks_like_federated_id(value: object) -> bool:
    return isinstance(value, str) and _SHAPE.match(value) is not None


def normalizar(tipo: str, numero: str) -> str:
    """The message that gets signed: ``CC|1144123456``."""

    document_type = (tipo or "").strip().upper()
    if document_type not in TYPES:
        raise FederatedIdError(f"tipo de documento fuera de {', '.join(TYPES)}: {tipo!r}")
    number = _SEPARATORS.sub("", unicodedata.normalize("NFKC", numero or "").strip())
    if not number:
        raise FederatedIdError("el numero de documento esta vacio")
    return f"{document_type}|{number}"


def id_federado(tipo: str, numero: str, *, secret: str | None = None) -> str:
    """The federated id of a document. Raises if there is no secret to compute it with."""

    key = secret if secret is not None else get_settings().federated_secret_value
    if not key:
        raise FederatedDisabledError("FEDERATED_SECRET no esta configurado")
    digest = hmac.new(
        key.encode("utf-8"), normalizar(tipo, numero).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest[:ID_LENGTH]


def for_patient(patient: Any, *, secret: str | None = None) -> str | None:
    """The federated id of a patient, or ``None`` when it cannot be computed.

    ``None`` without a secret, for a type outside CC/TI/CE/PA (a registro civil has no
    federated id) or when the stored document cannot be read. ``document_number`` is
    the model property, so an encrypted column is decrypted first.
    """

    if (secret if secret is not None else get_settings().federated_secret_value) is None:
        return None
    document_type = getattr(patient, "document_type", None)
    type_code = getattr(document_type, "value", document_type)
    try:
        return id_federado(str(type_code), patient.document_number, secret=secret)
    except Exception:  # noqa: BLE001 -- bad type/number, or a ciphertext that cannot be decrypted
        return None


# ---------------------------------------------------------------------------
# Document identifier (any-partner mode, INTEROP_ACCEPT_DOCUMENT_IDENTIFIER)
# ---------------------------------------------------------------------------
#
# Some teams cannot compute the federated id, so by AGREEMENT they send the national
# document (type and number) instead. That is a trade-off against data minimization (Ley 1581
# de 2012): the receiver derives the federated id itself and never stores the number.

MAX_DOCUMENT_CHARS = 40


def _document_type(identifier: dict[str, Any]) -> str | None:
    """The document type (CC, TI, CE or PA) an Identifier declares, or ``None``.

    Read from ``type.coding[].code``, then ``type.text``, then the last segment of
    ``system`` (``https://registro.example/CC``).
    """

    candidates: list[object] = []
    kind = identifier.get("type")
    if isinstance(kind, dict):
        codings = kind.get("coding")
        candidates += [c.get("code") for c in codings if isinstance(c, dict)] if isinstance(codings, list) else []
        candidates.append(kind.get("text"))
    system = identifier.get("system")
    if isinstance(system, str):
        candidates.append(system.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip().upper() in TYPES:
            return candidate.strip().upper()
    return None


def document_parts(identifier: Any) -> tuple[str, str] | None:
    """``(type, number)`` of an Identifier that carries a national document, else ``None``."""

    if not isinstance(identifier, dict):
        return None
    value = identifier.get("value")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    number = str(value).strip()
    document_type = _document_type(identifier)
    if document_type is None or not number or len(number) > MAX_DOCUMENT_CHARS:
        return None
    return document_type, number


def federated_id_of_document(identifier: Any, *, secret: str | None = None) -> str | None:
    """The federated id computed from a document Identifier; ``None`` when it cannot be."""

    parts = document_parts(identifier)
    if parts is None:
        return None
    try:
        return id_federado(parts[0], parts[1], secret=secret)
    except (FederatedIdError, FederatedDisabledError):
        return None


def document_identifier_for(patient: Any, *, system: str) -> dict[str, Any] | None:
    """The document Identifier of a patient for a partner that needs it, or ``None``.

    Only used when ``INTEROP_SEND_DOCUMENT_IDENTIFIER`` is on; the federated id is always
    sent as well.
    """

    document_type = getattr(patient, "document_type", None)
    type_code = str(getattr(document_type, "value", document_type) or "")
    try:
        normalized = normalizar(type_code, patient.document_number)
    except Exception:  # noqa: BLE001 -- unknown type, empty number or an unreadable ciphertext
        return None
    kind, number = normalized.split("|", 1)
    return {"system": system, "type": {"text": kind}, "value": number}


def patient_by_federated_id(db: Any, value: str) -> Any | None:
    """Resolve a received federated id to a live patient, or ``None``.

    1. **Indexed column.** ``patients.id_federado`` (filled on create/update).
    2. **Fallback for rows not backfilled.** The existing Neon rows have NULL there
       until the backfill is approved (docs/ENTREGA_FINAL.md, D1). For those, the id is
       computed in Python over the live patients whose ``id_federado`` is NULL, at most
       ``FALLBACK_MAX_CANDIDATES``. It costs a read of the table per unresolved message,
       which is fine at ~400 rows and is exactly what the backfill removes.

    Two patients with the same id (impossible for distinct documents) are ambiguous:
    the patient stays unknown rather than guessed.
    """

    from sqlalchemy import select

    from app.models.patient import Patient

    if not looks_like_federated_id(value):
        return None
    indexed = list(
        db.scalars(
            select(Patient)
            .where(Patient.deleted_at.is_(None), Patient.id_federado == value)
            .order_by(Patient.id)
            .limit(2)
        ).unique()
    )
    if len(indexed) == 1:
        return indexed[0]
    if len(indexed) > 1:
        return None

    candidates = db.scalars(
        select(Patient)
        .where(Patient.deleted_at.is_(None), Patient.id_federado.is_(None))
        .order_by(Patient.id)
        .limit(FALLBACK_MAX_CANDIDATES)
    ).unique()
    found = [p for p in candidates if hmac.compare_digest(for_patient(p) or "", value)]
    return found[0] if len(found) == 1 else None
