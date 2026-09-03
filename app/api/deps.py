"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.identity import User
from app.schemas.common import PageParams

DbSession = Annotated[Session, Depends(get_db)]

# auto_error=False so a missing header reaches our code and gets the same 401
# (with WWW-Authenticate) as a malformed token, instead of FastAPI's default 403.
bearer_scheme = HTTPBearer(auto_error=False)


def pagination(
    page: int = Query(default=1, ge=1, description="Numero de pagina, inicia en 1"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Registros por pagina"
    ),
) -> PageParams:
    """Validated pagination parameters shared by every list endpoint."""

    return PageParams(page=page, page_size=page_size)


PageQuery = Annotated[PageParams, Depends(pagination)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Resolve the Bearer token into a live ``User`` row.

    The token only proves identity. Role, scope and account status are re-read
    from the database on every request, so revoking an account or changing a
    role takes effect immediately instead of when the token expires.
    """

    if credentials is None:
        raise _unauthorized("Se requiere un token de acceso")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _unauthorized("Token invalido o expirado")

    try:
        user_id = uuid.UUID(payload.get("sub", ""))
    except ValueError:
        raise _unauthorized("Token invalido o expirado") from None

    user = db.scalar(
        select(User).options(joinedload(User.role)).where(User.id == user_id)
    )
    if user is None or not user.is_active:
        raise _unauthorized("La cuenta no existe o esta deshabilitada")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
