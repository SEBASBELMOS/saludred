"""Account administration endpoints.

Bloquear y desbloquear cuentas es competencia exclusiva del administrador: es
la contraparte de que el bloqueo automatico no venza solo. Si cualquiera
pudiera liberar una cuenta, el limite de intentos dejaria de ser una barrera.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.schemas.auth import AccountCreate, AccountLockAction, UserAccountRead
from app.schemas.common import Page
from app.services import accounts as accounts_service

router = APIRouter(prefix="/api/v1/admin/users", tags=["administracion de cuentas"])


def _to_read(user) -> UserAccountRead:
    return UserAccountRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role.code,
        is_active=user.is_active,
        failed_login_attempts=user.failed_login_attempts,
        is_locked=user.is_locked,
        locked_at=user.locked_at,
        last_login_at=user.last_login_at,
    )


@router.get(
    "",
    response_model=Page[UserAccountRead],
    summary="Listar cuentas y su estado de bloqueo (solo Admin)",
)
def list_accounts(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    only_locked: bool = Query(
        default=False, description="Mostrar unicamente las cuentas bloqueadas"
    ),
) -> Page[UserAccountRead]:
    authz.require_admin(user)
    items, total = accounts_service.list_users(
        db, page=params.page, page_size=params.page_size, only_locked=only_locked
    )
    return Page(
        items=[_to_read(u) for u in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.post(
    "/{user_id}/unlock",
    response_model=UserAccountRead,
    summary="Desbloquear una cuenta (solo Admin)",
)
def unlock_account(
    db: DbSession, user: CurrentUser, user_id: uuid.UUID, payload: AccountLockAction
) -> UserAccountRead:
    """Libera una cuenta bloqueada y pone su contador de intentos en cero."""

    authz.require_admin(user)
    objetivo = accounts_service.get_user(db, user_id)
    return _to_read(
        accounts_service.unlock_user(db, objetivo, actor=user, reason=payload.reason)
    )


@router.post(
    "/{user_id}/lock",
    response_model=UserAccountRead,
    summary="Bloquear una cuenta manualmente (solo Admin)",
)
def lock_account(
    db: DbSession, user: CurrentUser, user_id: uuid.UUID, payload: AccountLockAction
) -> UserAccountRead:
    """Bloquea sin esperar a los intentos fallidos.

    Util cuando se sabe que una credencial quedo expuesta y hay que cortarla
    antes de que alguien la use.
    """

    authz.require_admin(user)
    objetivo = accounts_service.get_user(db, user_id)
    return _to_read(
        accounts_service.lock_user(db, objetivo, actor=user, reason=payload.reason)
    )


@router.post(
    "",
    response_model=UserAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una cuenta (solo Admin)",
)
def create_account(db: DbSession, user: CurrentUser, payload: AccountCreate) -> UserAccountRead:
    """Operador de IPS, coordinador de EPS, administrador o cuenta de paciente.

    Una cuenta de paciente se vincula a su ficha por numero de documento: es la
    unica forma de que alguien vea "Mi informacion", y la decide la
    administracion, no el propio paciente.
    """

    authz.require_admin(user)
    return _to_read(accounts_service.create_user(db, payload, actor=user))
