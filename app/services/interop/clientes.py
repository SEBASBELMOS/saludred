"""Partner systems and their tokens (slide 28: OAuth 2.0 client credentials).

The JWT of part A identifies PEOPLE; this one identifies SYSTEMS. The receiver registers
the sender, hands it a ``client_id`` and a ``client_secret`` (and, apart, a signing
secret) over a private channel, and grants ONLY the registered scopes -- however much the
sender asks for (SMART on FHIR v1 notation: ``system/Observation.write``).

The system token is a second kind of token that cannot be confused with the first:

* ``aud`` is ``saludred-interop`` (the user token has ``saludred-api``), so each decoder
  rejects the other's tokens;
* ``sub`` is the ``client_id``, never a user id, and it carries ``scope``;
* HS256 only, 10 minutes.

Storage of the signing secret: the receiver must recompute the HMAC, so it cannot be
hashed. It is stored AES-256-GCM encrypted (``field_crypto``, AAD bound to the client)
whenever ``FIELD_ENCRYPTION_KEYS`` is set. Without keys registration REFUSES, unless the
operator explicitly accepts a plain-text secret (``allow_plaintext``): failing closed is
safer than silently keeping a secret readable in the database.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import field_crypto
from app.core.config import get_settings
from app.core.security import (
    JWT_ALGORITHM,
    burn_password_check,
    hash_password,
    verify_password,
)
from app.models.interop import InteropClient
from app.services.errors import ConflictError, InvalidValueError

SCOPE_OBSERVATION = "system/Observation.write"
SCOPE_SERVICE_REQUEST = "system/ServiceRequest.write"
SCOPE_BUNDLE = "system/Bundle.write"
SCOPE_TASK = "system/Task.write"
KNOWN_SCOPES = (SCOPE_OBSERVATION, SCOPE_SERVICE_REQUEST, SCOPE_BUNDLE, SCOPE_TASK)

_CLIENT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
PLAIN_PREFIX = "plain."


class SigningSecretUnavailableError(RuntimeError):
    """The stored signing secret cannot be read (keys missing or value altered)."""


def _aad(client_id: str) -> bytes:
    return f"interop|{client_id}|firma".encode("utf-8")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisteredClient:
    """What the operator sees ONCE, at registration. The secrets are never stored in clear
    (except an explicitly accepted plain-text signing secret) nor shown again."""

    client: InteropClient
    client_secret: str
    signing_secret: str


def parse_scopes(raw: str | list[str] | None) -> list[str]:
    items = raw.replace(",", " ").split() if isinstance(raw, str) else list(raw or [])
    return list(dict.fromkeys(item.strip() for item in items if item and item.strip()))


def seal_signing_secret(client_id: str, signing_secret: str, *, allow_plaintext: bool = False) -> str:
    if field_crypto.encryption_enabled():
        return field_crypto.encrypt_field(signing_secret, _aad(client_id))
    if not allow_plaintext:
        raise InvalidValueError(
            "FIELD_ENCRYPTION_KEYS no esta configurado: el secreto de firma no puede guardarse "
            "cifrado. Configure las llaves, o acepte el riesgo de guardarlo en texto plano "
            "(--permitir-texto-plano)"
        )
    return PLAIN_PREFIX + signing_secret


def open_signing_secret(client: InteropClient) -> str:
    stored = client.signing_secret
    if stored.startswith(PLAIN_PREFIX):
        return stored[len(PLAIN_PREFIX):]
    try:
        return field_crypto.decrypt_field(stored, _aad(client.client_id))
    except field_crypto.FieldCryptoError as exc:
        raise SigningSecretUnavailableError(str(exc)) from exc


def register_client(
    db: Session,
    *,
    client_id: str,
    name: str,
    scopes: list[str],
    allow_plaintext: bool = False,
    client_secret: str | None = None,
    signing_secret: str | None = None,
    outbound_env_prefix: str | None = None,
) -> RegisteredClient:
    """Create a partner. Secrets are generated here unless the caller passes them.

    ``outbound_env_prefix`` names the environment variables SaludRed uses to SEND to this
    partner (``<PREFIX>_BASE_URL``, ``_CLIENT_ID``, ``_CLIENT_SECRET``, ``_SIGNING_SECRET``);
    it never holds the credentials themselves.
    """

    client_id = client_id.strip().lower()
    if outbound_env_prefix is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", outbound_env_prefix):
        raise InvalidValueError("El prefijo de envio debe ser MAYUSCULAS, digitos y guion bajo (ej. SOCIO_A)")
    if not _CLIENT_ID.match(client_id):
        raise InvalidValueError("client_id: 3 a 64 caracteres [a-z0-9._-], empezando por letra o numero")
    if not scopes:
        raise InvalidValueError("Registre al menos un alcance")
    unknown = [s for s in scopes if s not in KNOWN_SCOPES]
    if unknown:
        raise InvalidValueError(f"Alcance desconocido: {', '.join(unknown)} (validos: {', '.join(KNOWN_SCOPES)})")
    if db.scalar(select(InteropClient.id).where(InteropClient.client_id == client_id)) is not None:
        raise ConflictError("Ya existe un cliente con ese client_id")

    client_secret = client_secret or secrets.token_urlsafe(32)
    signing_secret = signing_secret or secrets.token_urlsafe(32)
    client = InteropClient(
        id=uuid.uuid4(),
        client_id=client_id,
        name=name.strip()[:200] or client_id,
        secret_hash=hash_password(client_secret),
        scopes=scopes,
        signing_secret=seal_signing_secret(client_id, signing_secret, allow_plaintext=allow_plaintext),
        is_active=True,
        outbound_env_prefix=outbound_env_prefix,
    )
    db.add(client)
    db.commit()
    return RegisteredClient(client, client_secret, signing_secret)


def list_clients(db: Session) -> list[InteropClient]:
    return list(db.scalars(select(InteropClient).order_by(InteropClient.client_id)))


def get_active_client(db: Session, client_id: str) -> InteropClient | None:
    client = db.scalar(select(InteropClient).where(InteropClient.client_id == client_id))
    return client if client is not None and client.is_active else None


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """RFC 6749 error: ``error`` is ``invalid_client``, ``invalid_scope``..."""

    def __init__(self, status: int, error: str, description: str) -> None:
        super().__init__(description)
        self.status = status
        self.error = error
        self.description = description


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int
    scope: str
    client: InteropClient


def authenticate_client(db: Session, client_id: str | None, client_secret: str | None) -> InteropClient:
    """Verify ``client_id`` + ``client_secret``. Unknown client and wrong secret look alike."""

    client = get_active_client(db, client_id or "") if client_id else None
    if client is None:
        # Same cost as a real verification: the answer time does not tell which ids exist.
        burn_password_check(client_secret or "")
        raise TokenError(401, "invalid_client", "Credenciales de cliente invalidas")
    if not client_secret or not verify_password(client_secret, client.secret_hash):
        raise TokenError(401, "invalid_client", "Credenciales de cliente invalidas")
    return client


def granted_scopes(client: InteropClient, requested: list[str]) -> list[str]:
    """Registered scopes, narrowed to the requested ones when any were requested."""

    registered = [s for s in client.scopes if isinstance(s, str)]
    if not requested:
        return registered
    return [s for s in requested if s in registered]


def issue_token(
    db: Session,
    *,
    client_id: str | None,
    client_secret: str | None,
    scope: str | None,
    grant_type: str | None,
    now: datetime | None = None,
) -> IssuedToken:
    if grant_type != "client_credentials":
        raise TokenError(400, "unsupported_grant_type", "grant_type debe ser client_credentials")
    client = authenticate_client(db, client_id, client_secret)
    requested = parse_scopes(scope)
    granted = granted_scopes(client, requested)
    if requested and not granted:
        raise TokenError(400, "invalid_scope", "Ninguno de los alcances pedidos esta registrado para este cliente")

    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    ttl = settings.interop_token_ttl_seconds
    claims: dict[str, Any] = {
        "sub": client.client_id,
        "scope": " ".join(granted),
        "iss": settings.jwt_issuer,
        "aud": settings.interop_audience,
        "iat": moment,
        "exp": moment + timedelta(seconds=ttl),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, settings.interop_signing_key, algorithm=JWT_ALGORITHM)
    client.last_used_at = moment
    db.commit()
    return IssuedToken(token, ttl, " ".join(granted), client)


LAST_USED_MIN_INTERVAL = timedelta(minutes=1)


def touch_last_used(db: Session, client: InteropClient, *, now: datetime | None = None) -> bool:
    """Record that ``client`` authenticated, at most once per minute (an API key has no token
    request to stamp it). ``True`` when the row was written (and committed)."""

    moment = now or datetime.now(timezone.utc)
    last = client.last_used_at
    if last is not None:
        if last.tzinfo is None:  # SQLite returns naive datetimes
            last = last.replace(tzinfo=timezone.utc)
        if moment - last < LAST_USED_MIN_INTERVAL:
            return False
    client.last_used_at = moment
    db.commit()
    return True


def decode_system_token(token: str) -> dict[str, Any] | None:
    """The claims of a valid system token, or ``None`` (any user token fails on ``aud``)."""

    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.interop_signing_key,
            algorithms=[JWT_ALGORITHM],
            audience=settings.interop_audience,
            issuer=settings.jwt_issuer,
            options={f"require_{c}": True for c in ("sub", "iat", "exp", "iss", "aud")},
        )
    except JWTError:
        return None
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("scope"), str):
        return None
    return claims
