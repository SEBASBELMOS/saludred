"""Password hashing and JWT issuing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import get_settings

settings = get_settings()

# Argon2id first: it is what ``hash`` produces and what a stored hash is upgraded
# to. bcrypt stays only to VERIFY the hashes created before the change; the first
# successful login of each account rewrites it as Argon2id (``verify_and_update``).
# Argon2id is slow AND memory-hard, which is what makes a GPU rig expensive; plain
# text or a fast digest such as SHA-256 would not be acceptable even for demo data.
_hasher = PasswordHash((Argon2Hasher(), BcryptHasher()))

# Verified when the account does not exist, so a missing user costs about the same
# time as a wrong password and the response time does not reveal which usernames exist.
_decoy_hash: str | None = None

# The ONLY algorithm accepted when decoding. It is a constant on purpose: a setting
# would let a misconfiguration (or ``none``) reopen the algorithm-confusion attack.
JWT_ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "role", "iat", "exp", "iss", "aud")
_LIBRARY_REQUIRED = ("sub", "iat", "exp", "iss", "aud")


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_and_update_password(
    plain_password: str, password_hash: str
) -> tuple[bool, str | None]:
    """Check a password; return ``(valid, new_hash)``.

    ``new_hash`` is not ``None`` only when the password is right AND the stored
    hash is not the current scheme (bcrypt) or uses outdated cost parameters: the
    caller must save it, so the system hardens itself without asking anyone to
    change their password. A hash of an unknown format is simply "not valid".
    """

    try:
        return _hasher.verify_and_update(plain_password, password_hash)
    except UnknownHashError:
        return False, None


def verify_password(plain_password: str, password_hash: str) -> bool:
    return verify_and_update_password(plain_password, password_hash)[0]


def burn_password_check(plain_password: str) -> None:
    """Spend the cost of one verification without a real account behind it."""

    global _decoy_hash
    if _decoy_hash is None:
        _decoy_hash = _hasher.hash("decoy-password-never-matches")
    verify_password(plain_password, _decoy_hash)


def create_access_token(subject: uuid.UUID, role_code: str) -> str:
    """Issue a signed JWT for an authenticated user.

    The token carries only the opaque user id (``sub``) and the role, plus the
    registered claims that bound it: issue time, expiry in minutes, issuer and
    audience. The role travels inside the token, but it is re-read from the
    database on every request: a token is a claim of identity, never the source
    of truth for permissions.
    """

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role_code,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the token payload, or ``None`` when it is invalid or expired.

    One algorithm (``HS256``, hard-coded) closes the ``alg: none`` and the
    RS256-as-HS256 attacks; signature, expiry, issuer and audience are verified,
    and every claim of ``REQUIRED_CLAIMS`` must be present.
    """

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[JWT_ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={f"require_{claim}": True for claim in _LIBRARY_REQUIRED},
        )
    except JWTError:
        return None
    # ``role`` is a private claim: the library cannot require it, so it is checked here.
    if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("role"), str):
        return None
    return payload
