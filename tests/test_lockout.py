"""Account lockout after repeated failed logins.

The schema is built in SQLite in memory: these checks are about transaction
behaviour -- what survives a failed login, what gets written before the
exception propagates -- and that cannot be tested against mocks.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import hash_password
from app.models import AuditLog, Base, Role, User
from app.models.enums import AuditAction, RoleCode
from app.services import accounts
from app.services.auth import (
    MAX_FAILED_ATTEMPTS,
    AccountLockedError,
    InvalidCredentialsError,
    authenticate,
    remaining_attempts,
)
from app.services.errors import ConflictError

PASSWORD = "Correcta2026!"
WRONG = "incorrecta"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


@pytest.fixture
def user(db: Session) -> User:
    role = Role(code=RoleCode.IPS_CLINICAL_OPERATOR, name="Operador", description="Rol de prueba")
    db.add(role)
    db.flush()
    account = User(
        username="operador.prueba",
        full_name="Operador de Prueba",
        email="operador.prueba@saludred.local",
        password_hash=hash_password(PASSWORD),
        role_id=role.id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@pytest.fixture
def admin(db: Session) -> User:
    role = Role(code=RoleCode.ADMIN, name="Administrador", description="Rol de prueba")
    db.add(role)
    db.flush()
    account = User(
        username="admin.prueba",
        full_name="Admin de Prueba",
        email="admin.prueba@saludred.local",
        password_hash=hash_password(PASSWORD),
        role_id=role.id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _fail(db: Session, username: str = "operador.prueba") -> None:
    with pytest.raises((InvalidCredentialsError, AccountLockedError)):
        authenticate(db, username, WRONG)


def test_a_new_account_starts_with_no_failed_attempts(user: User) -> None:
    """The counter must be 0, never None.

    ``default=`` on a column applies when the row is inserted, not when the
    object is built. If the attribute were still None here, the first ``+= 1``
    would raise a TypeError and the lockout would never engage.
    """

    assert user.failed_login_attempts == 0
    assert user.locked_at is None
    assert user.is_locked is False


def test_a_correct_password_is_accepted(db: Session, user: User) -> None:
    assert authenticate(db, user.username, PASSWORD).id == user.id


def test_each_failure_advances_the_counter(db: Session, user: User) -> None:
    for expected in (1, 2):
        _fail(db)
        db.refresh(user)
        assert user.failed_login_attempts == expected
        assert user.is_locked is False
        assert remaining_attempts(user) == MAX_FAILED_ATTEMPTS - expected


def test_the_third_failure_locks_the_account(db: Session, user: User) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    db.refresh(user)
    assert user.is_locked is True
    assert user.failed_login_attempts == MAX_FAILED_ATTEMPTS


def test_the_counter_survives_the_failed_transaction(db: Session, user: User) -> None:
    """The evidence is committed before the exception propagates.

    If the rollback were allowed to drag it along, every attempt would undo
    its own record and the counter would sit at zero forever -- the lockout
    would look implemented and do nothing.
    """

    _fail(db)
    db.rollback()
    db.expire_all()
    fresh = db.scalar(select(User).where(User.id == user.id))
    assert fresh.failed_login_attempts == 1


def test_a_locked_account_is_rejected_even_with_the_right_password(
    db: Session, user: User
) -> None:
    """Checking the password first would leak whether it was correct.

    That is exactly the confirmation someone testing stolen credentials is
    looking for, and a locked account must not hand it over.
    """

    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    with pytest.raises(AccountLockedError):
        authenticate(db, user.username, PASSWORD)


def test_a_successful_login_clears_earlier_failures(db: Session, user: User) -> None:
    """Someone who mistypes once on Monday should not be one slip from lockout
    for the rest of the year."""

    _fail(db)
    _fail(db)
    authenticate(db, user.username, PASSWORD)
    db.refresh(user)
    assert user.failed_login_attempts == 0


def test_an_unknown_username_never_reveals_itself(db: Session) -> None:
    """Same exception as a wrong password, so the two cannot be told apart."""

    with pytest.raises(InvalidCredentialsError):
        authenticate(db, "no.existe", WRONG)


def test_an_unknown_username_is_still_audited(db: Session) -> None:
    with pytest.raises(InvalidCredentialsError):
        authenticate(db, "no.existe", WRONG)
    entry = db.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED)
    )
    assert entry is not None
    assert entry.entity_id is None


def test_every_outcome_leaves_an_audit_entry(db: Session, user: User) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    actions = set(db.scalars(select(AuditLog.action)).all())
    assert AuditAction.LOGIN_FAILED in actions
    assert AuditAction.ACCOUNT_LOCKED in actions


def test_an_inactive_account_does_not_accumulate_attempts(
    db: Session, user: User
) -> None:
    """A disabled account cannot be locked, because it is already closed.

    Counting attempts on it would only add noise to the audit trail.
    """

    user.is_active = False
    db.commit()
    _fail(db)
    db.refresh(user)
    assert user.failed_login_attempts == 0


def test_only_an_unlock_brings_the_account_back(
    db: Session, user: User, admin: User
) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    accounts.unlock_user(db, user, actor=admin, reason="verificado por telefono")
    assert authenticate(db, user.username, PASSWORD).id == user.id


def test_unlocking_resets_the_counter(db: Session, user: User, admin: User) -> None:
    """Otherwise the next typo re-locks the account immediately and the
    unlock would have achieved nothing."""

    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    accounts.unlock_user(db, user, actor=admin, reason=None)
    db.refresh(user)
    assert user.failed_login_attempts == 0
    assert remaining_attempts(user) == MAX_FAILED_ATTEMPTS

    _fail(db)
    db.refresh(user)
    assert user.is_locked is False


def test_unlocking_an_open_account_is_refused(
    db: Session, user: User, admin: User
) -> None:
    with pytest.raises(ConflictError):
        accounts.unlock_user(db, user, actor=admin, reason=None)


def test_the_unlock_records_who_did_it(db: Session, user: User, admin: User) -> None:
    """The audit answer to 'who let this account back in' must be a person."""

    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    accounts.unlock_user(db, user, actor=admin, reason="verificado por telefono")
    entry = db.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.ACCOUNT_UNLOCKED)
    )
    assert entry is not None
    assert entry.user_id == admin.id


def test_an_administrator_cannot_lock_themselves_out(
    db: Session, admin: User
) -> None:
    """It would take a second administrator to undo, and there may not be one."""

    with pytest.raises(ConflictError):
        accounts.lock_user(db, admin, actor=admin, reason="por error")


def test_a_manual_lock_blocks_the_next_login(
    db: Session, user: User, admin: User
) -> None:
    accounts.lock_user(db, user, actor=admin, reason="investigacion en curso")
    with pytest.raises(AccountLockedError):
        authenticate(db, user.username, PASSWORD)


def test_only_locked_accounts_are_listed_when_asked(
    db: Session, user: User, admin: User
) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        _fail(db)
    locked, total = accounts.list_users(db, page=1, page_size=50, only_locked=True)
    assert total == 1
    assert [item.username for item in locked] == [user.username]

    everyone, total_all = accounts.list_users(db, page=1, page_size=50)
    assert total_all == 2
    assert {item.username for item in everyone} == {user.username, admin.username}
