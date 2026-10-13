"""Authentication: credential verification, lockout and login auditing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.security import verify_password
from app.events.bus import DomainEvent, publish
from app.models.enums import AuditAction
from app.models.identity import User
from app.services import recipients, trail
from app.services.errors import commit

logger = logging.getLogger(__name__)

# Intentos permitidos antes de bloquear. Tres es un equilibrio conocido: deja
# margen para un error de tipeo o un teclado en otro idioma, y corta mucho
# antes de que probar claves al azar sea util.
MAX_FAILED_ATTEMPTS = 3


class InvalidCredentialsError(Exception):
    """Wrong username or password, or a disabled account.

    Deliberadamente NO es un ``ServiceError``: la ruta lo convierte en un 401
    con un mensaje unico. Cual de las tres condiciones fallo nunca se revela,
    porque "el usuario existe pero la clave esta mal" es justo la confirmacion
    que busca quien esta probando credenciales.
    """


class AccountLockedError(Exception):
    """The account is locked and only an administrator can release it.

    Este caso SI se distingue del anterior, y es una decision deliberada: la
    persona legitima necesita saber por que no entra y a quien pedirle ayuda.
    El dato que se filtra --que esa cuenta existe-- ya se lo habia ganado
    quien fallo tres veces seguidas.
    """


def authenticate(db: Session, username: str, password: str) -> User:
    """Verify credentials, applying the lockout policy.

    El orden de las comprobaciones importa:

    1. Si la cuenta esta bloqueada, se rechaza sin siquiera mirar la clave.
       Comprobarla primero permitiria distinguir "bloqueada con clave buena"
       de "bloqueada con clave mala", que es informacion regalada.
    2. Si las credenciales fallan, se suma el intento y, al llegar al limite,
       se bloquea la cuenta en la misma transaccion.
    3. Si el ingreso es correcto, el contador vuelve a cero: los intentos
       fallidos sueltos de quien simplemente se equivoco no se acumulan para
       siempre.

    Cada desenlace queda auditado, incluido el fallido, que se registra sin
    ``user_id`` cuando el usuario ni siquiera existe.
    """

    user = db.scalar(
        select(User).options(joinedload(User.role)).where(User.username == username)
    )

    if user is not None and user.is_locked:
        trail.audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="users",
            entity_id=user.id,
            actor=None,
            metadata={"username": username, "outcome": "locked"},
        )
        commit(db)
        raise AccountLockedError

    credentials_ok = (
        user is not None
        and user.is_active
        and verify_password(password, user.password_hash)
    )

    if not credentials_ok:
        locked_now = False
        if user is not None and user.is_active:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_at = datetime.now(timezone.utc)
                locked_now = True

        trail.audit(
            db,
            action=AuditAction.LOGIN_FAILED,
            entity_type="users",
            entity_id=user.id if user else None,
            actor=None,
            metadata={
                "username": username,
                "outcome": "invalid_credentials",
                "failed_attempts": user.failed_login_attempts if user else None,
            },
        )
        if locked_now:
            trail.audit(
                db,
                action=AuditAction.ACCOUNT_LOCKED,
                entity_type="users",
                entity_id=user.id,
                actor=None,
                metadata={
                    "username": username,
                    "reason": f"{MAX_FAILED_ATTEMPTS} intentos fallidos consecutivos",
                },
            )
        # El commit va antes de propagar la excepcion: si se dejara que el
        # rollback la arrastre, se perderia la evidencia del intento y el
        # contador nunca avanzaria.
        commit(db)

        if locked_now:
            _announce_lock(db, user)
            raise AccountLockedError
        raise InvalidCredentialsError

    user.failed_login_attempts = 0
    user.last_login_at = datetime.now(timezone.utc)
    trail.audit(
        db,
        action=AuditAction.LOGIN,
        entity_type="users",
        entity_id=user.id,
        actor=user,
        metadata={"username": username, "outcome": "success"},
    )
    commit(db)
    return user


def _announce_lock(db: Session, user: User) -> None:
    """R19 flow 4: tell every admin which account was locked.

    Published AFTER the lock is committed, so a reactor never sees (or leaks) a
    lock that was rolled back. It is best effort: the bus isolates a failing
    reactor, and a failure here must never change what the login caller sees,
    which is the same locked answer either way. The caller learns nothing new:
    the 423 already says the account is locked.
    """

    try:
        publish(
            DomainEvent(
                tipo="cuenta_bloqueada",
                mensaje=(
                    f"La cuenta {user.username} fue bloqueada tras "
                    f"{MAX_FAILED_ATTEMPTS} intentos fallidos consecutivos"
                ),
                recipients=tuple(db.scalars(recipients.admins_stmt())),
                clinical=False,
                datos={
                    "usuario": user.username,
                    "usuario_id": str(user.id),
                    "intentos": MAX_FAILED_ATTEMPTS,
                },
            ),
            db,
        )
    except Exception:  # noqa: BLE001 -- never alter the login answer
        logger.exception("Could not announce the lock of %s", user.username)


def remaining_attempts(user: User) -> int:
    """How many tries are left before the account locks."""

    return max(0, MAX_FAILED_ATTEMPTS - user.failed_login_attempts)
