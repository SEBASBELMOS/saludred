"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import PageParams

DbSession = Annotated[Session, Depends(get_db)]


def pagination(
    page: int = Query(default=1, ge=1, description="Numero de pagina, inicia en 1"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Registros por pagina"
    ),
) -> PageParams:
    """Validated pagination parameters shared by every list endpoint."""

    return PageParams(page=page, page_size=page_size)


PageQuery = Annotated[PageParams, Depends(pagination)]


def get_current_user() -> None:
    """Placeholder for the Phase 3 JWT authentication dependency.

    Declared now so routers can adopt it without churn later. It fails closed:
    no route depends on it in Phase 2, but once wired it will never let
    anonymous traffic through by default.
    """

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Autenticacion no implementada aun (Fase 3)",
    )
