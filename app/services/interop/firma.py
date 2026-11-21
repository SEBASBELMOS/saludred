"""Per-message HMAC signature (slide 29).

::

    firma = HMAC-SHA256(secreto_de_firma, fecha + "." + nonce + "." + cuerpo)

sent in three headers::

    X-Interop-Fecha: <unix seconds>
    X-Interop-Nonce: <random, single use>
    X-Interop-Firma: v1=<hex>

* The BODY is inside the signature: nobody can turn a 182 into an 82 on the way.
* The DATE with a tolerance (300 s) stops an old message from being resent.
* The NONCE stops a recent message from being resent inside that window. The date alone
  would not: the same message could be replayed any number of times for five minutes.

The signature is computed over the RAW bytes of the body, never over a re-serialized
JSON: any difference in spacing or key order would change it. It is compared with
``hmac.compare_digest``. The signing secret is per client and different from the
``client_secret`` (slide 33): leaking one does not give the other.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.interop import InteropNonce

HEADER_DATE = "X-Interop-Fecha"
HEADER_NONCE = "X-Interop-Nonce"
HEADER_SIGNATURE = "X-Interop-Firma"
VERSION = "v1"

_NONCE = re.compile(r"^[A-Za-z0-9._\-]{8,128}$")
_SIGNATURE = re.compile(r"^v1=([0-9a-fA-F]{64})$")


class SignatureError(Exception):
    """The message is refused: ``status`` is 401 (bad/stale signature) or 409 (replay)."""

    def __init__(self, status: int, diagnostics: str) -> None:
        super().__init__(diagnostics)
        self.status = status
        self.diagnostics = diagnostics


def compute(secret: str, fecha: int | str, nonce: str, body: bytes) -> str:
    """The hex HMAC-SHA256 over ``fecha.nonce.`` followed by the raw body."""

    message = f"{fecha}.{nonce}.".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


def sign(secret: str, body: bytes, *, now: float | None = None, nonce: str | None = None) -> dict[str, str]:
    """The three headers for ``body`` (what the sender attaches)."""

    fecha = int(now if now is not None else time.time())
    nonce = nonce or new_nonce()
    return {
        HEADER_DATE: str(fecha),
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: f"{VERSION}={compute(secret, fecha, nonce, body)}",
    }


@dataclass(frozen=True)
class SignedHeaders:
    fecha: int
    nonce: str
    firma: str  # hex


def parse_headers(fecha: str | None, nonce: str | None, firma: str | None) -> SignedHeaders:
    """Read the three headers. Anything missing or malformed is a 401."""

    if not fecha or not nonce or not firma:
        raise SignatureError(
            401, f"Faltan cabeceras de firma: {HEADER_DATE}, {HEADER_NONCE} y {HEADER_SIGNATURE}"
        )
    try:
        moment = int(fecha.strip())
    except ValueError:
        raise SignatureError(401, f"{HEADER_DATE} debe ser un entero en segundos Unix") from None
    if not _NONCE.match(nonce.strip()):
        raise SignatureError(401, f"{HEADER_NONCE} invalido (8 a 128 caracteres alfanumericos)")
    match = _SIGNATURE.match(firma.strip())
    if match is None:
        raise SignatureError(401, f"{HEADER_SIGNATURE} debe tener la forma v1=<hex de 64 caracteres>")
    return SignedHeaders(moment, nonce.strip(), match.group(1).lower())


def check(secret: str, headers: SignedHeaders, body: bytes, *, now: float | None = None) -> None:
    """Date window, then signature. Raises :class:`SignatureError` (401)."""

    tolerance = get_settings().interop_signature_tolerance_seconds
    clock = now if now is not None else time.time()
    if abs(clock - headers.fecha) > tolerance:
        raise SignatureError(401, f"Fecha fuera de la ventana de {tolerance // 60 or 1} minutos "
                             f"({tolerance} s)")
    expected = compute(secret, headers.fecha, headers.nonce, body)
    if not hmac.compare_digest(expected, headers.firma):
        raise SignatureError(401, "Firma invalida: el cuerpo o el secreto no coinciden")


def register_nonce(db: Session, client_id: str, nonce: str, *, now: datetime | None = None) -> None:
    """Remember a nonce; a repeat inside the window is a 409.

    The UNIQUE (client, nonce) constraint makes the check atomic: two simultaneous
    copies of the same message cannot both pass. Nonces older than twice the tolerance
    are purged on every call (a message that old fails the date check anyway).
    """

    moment = now or datetime.now(timezone.utc)
    retention = timedelta(seconds=get_settings().interop_signature_tolerance_seconds * 2)
    db.execute(delete(InteropNonce).where(InteropNonce.seen_at < moment - retention))
    db.add(InteropNonce(client_id=client_id, nonce=nonce, seen_at=moment))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SignatureError(409, "Mensaje repetido: ese nonce ya se uso") from None
