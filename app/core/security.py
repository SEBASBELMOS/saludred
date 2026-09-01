"""Password hashing and JWT issuing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# bcrypt: deliberately slow, which is the property that matters for stored
# passwords. Plain text or a fast digest such as SHA-256 would not be acceptable
# even for synthetic demo accounts.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(
    subject: uuid.UUID, role_code: str, extra_claims: dict[str, Any] | None = None
) -> str:
    """Issue a signed JWT for an authenticated user.

    The role travels inside the token, but it is re-read from the database on
    every request: a token is a claim of identity, never the source of truth for
    permissions.
    """

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role_code,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the token payload, or ``None`` when it is invalid or expired."""

    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
