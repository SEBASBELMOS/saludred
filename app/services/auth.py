"""Authentication: credential verification and login auditing."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.security import verify_password
from app.models.enums import AuditAction
from app.models.identity import User
from app.services import trail
from app.services.errors import commit


class InvalidCredentialsError(Exception):
    """Wrong username or password, or a disabled account.

    Deliberately NOT a ``ServiceError``: the route converts it to a 401 with a
    single generic message. Which of the three conditions failed is never
    revealed to the caller, because "user exists but wrong password" is exactly
    the confirmation an attacker is looking for.
    """


def authenticate(db: Session, username: str, password: str) -> User:
    """Verify credentials and record the attempt in the audit log.

    Both outcomes are audited: a failed attempt (with no user id, since nobody
    authenticated) and a successful login. The failure is committed before the
    exception propagates -- an aborted transaction that erased the evidence of
    the attempt would defeat the point of auditing it.
    """

    user = db.scalar(
        select(User).options(joinedload(User.role)).where(User.username == username)
    )

    if user is None or not user.is_active or not verify_password(
        password, user.password_hash
    ):
        trail.audit(
            db,
            action=AuditAction.LOGIN,
            entity_type="users",
            entity_id=user.id if user else None,
            actor=None,
            metadata={"username": username, "outcome": "failure"},
        )
        commit(db)
        raise InvalidCredentialsError

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
