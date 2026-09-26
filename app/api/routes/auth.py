"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.security import create_access_token
from app.schemas.auth import CurrentUserRead, LoginRequest, TokenResponse
from app.services.auth import (
    AccountLockedError,
    InvalidCredentialsError,
    authenticate,
)

router = APIRouter(prefix="/api/v1/auth", tags=["autenticacion"])

settings = get_settings()


@router.post("/login", response_model=TokenResponse, summary="Iniciar sesion")
def login(db: DbSession, payload: LoginRequest) -> TokenResponse:
    try:
        user = authenticate(db, payload.username, payload.password)
    except AccountLockedError:
        # 423 Locked, y no 401: el recurso existe y las credenciales podrian
        # ser correctas, pero la cuenta esta retenida. Distinguirlo le dice a
        # la persona legitima por que no entra y a quien recurrir.
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                "Cuenta bloqueada por intentos fallidos. "
                "Solicite el desbloqueo a un administrador."
            ),
        ) from None
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
        ) from None

    token = create_access_token(user.id, user.role.code.value)
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.access_token_expire_minutes,
        role=user.role.code,
    )


@router.get("/me", response_model=CurrentUserRead, summary="Cuenta autenticada")
def me(user: CurrentUser) -> CurrentUserRead:
    return CurrentUserRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role.code,
        organization_id=user.organization_id,
        patient_id=user.patient_id,
    )
