"""Password hashing (Argon2id) and the JWT lifecycle.

The token tests attack the module the way the slides do: a role changed without
re-signing, a token signed with another key, ``alg: none``, an expired token, a wrong
audience or issuer, and a token signed with an algorithm that is not HS256.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from jose import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.config import Settings
from app.models import AuditLog, Base, Role, User
from app.models.enums import AuditAction, RoleCode
from app.services.auth import (
    AccountLockedError,
    InvalidCredentialsError,
    authenticate,
)


class TestPasswords:
    def test_hash_roundtrip(self):
        digest = security.hash_password("Demo2026!")
        assert digest != "Demo2026!"
        assert security.verify_password("Demo2026!", digest)
        assert not security.verify_password("otra-clave", digest)

    def test_new_hashes_are_argon2id_with_a_random_salt(self):
        first = security.hash_password("Demo2026!")
        second = security.hash_password("Demo2026!")
        assert first.startswith("$argon2id$")
        assert first != second

    def test_a_current_hash_needs_no_update(self):
        digest = security.hash_password("Demo2026!")
        assert security.verify_and_update_password("Demo2026!", digest) == (True, None)

    def test_a_legacy_bcrypt_hash_still_verifies_and_asks_to_be_upgraded(self):
        legacy = bcrypt.hashpw(b"Demo2026!", bcrypt.gensalt(rounds=4)).decode()
        valid, new_hash = security.verify_and_update_password("Demo2026!", legacy)
        assert valid is True
        assert new_hash is not None and new_hash.startswith("$argon2id$")
        assert security.verify_password("Demo2026!", new_hash)

    def test_a_wrong_password_never_yields_a_new_hash(self):
        legacy = bcrypt.hashpw(b"Demo2026!", bcrypt.gensalt(rounds=4)).decode()
        assert security.verify_and_update_password("incorrecta", legacy) == (False, None)

    def test_an_unknown_hash_format_is_just_invalid(self):
        assert security.verify_and_update_password("x", "not-a-hash") == (False, None)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def _account(db: Session, password_hash: str) -> User:
    role = Role(code=RoleCode.IPS_CLINICAL_OPERATOR, name="Operador", description="Rol de prueba")
    db.add(role)
    db.flush()
    user = User(
        username="operador.hash",
        full_name="Operador Hash",
        email="operador.hash@saludred.local",
        password_hash=password_hash,
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    return user


class TestLazyRehash:
    """Legacy bcrypt hashes are rewritten as Argon2id on the first CORRECT login."""

    def test_a_bcrypt_account_logs_in_and_is_rewritten_as_argon2id(self, db: Session) -> None:
        legacy = bcrypt.hashpw(b"Correcta2026!", bcrypt.gensalt(rounds=4)).decode()
        user = _account(db, legacy)

        logged = authenticate(db, "operador.hash", "Correcta2026!")

        db.refresh(user)
        assert logged.id == user.id
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != legacy
        # The new hash is persisted (same transaction as the login) and still works.
        assert authenticate(db, "operador.hash", "Correcta2026!").id == user.id
        entry = db.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.LOGIN).order_by(AuditLog.seq)
        ).first()
        assert entry.metadata_json["password_rehashed"] is True

    def test_a_failed_login_rewrites_nothing(self, db: Session) -> None:
        legacy = bcrypt.hashpw(b"Correcta2026!", bcrypt.gensalt(rounds=4)).decode()
        user = _account(db, legacy)

        with pytest.raises(InvalidCredentialsError):
            authenticate(db, "operador.hash", "incorrecta")

        db.refresh(user)
        assert user.password_hash == legacy

    def test_a_locked_account_is_not_rehashed_even_with_the_right_password(self, db: Session) -> None:
        legacy = bcrypt.hashpw(b"Correcta2026!", bcrypt.gensalt(rounds=4)).decode()
        user = _account(db, legacy)
        user.locked_at = datetime.now(timezone.utc)
        db.commit()

        with pytest.raises(AccountLockedError):
            authenticate(db, "operador.hash", "Correcta2026!")

        db.refresh(user)
        assert user.password_hash == legacy

    def test_a_current_hash_is_left_alone(self, db: Session) -> None:
        current = security.hash_password("Correcta2026!")
        user = _account(db, current)
        authenticate(db, "operador.hash", "Correcta2026!")
        db.refresh(user)
        assert user.password_hash == current

    def test_an_unknown_user_costs_a_verification_too(self, db: Session, monkeypatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(security, "verify_password", lambda p, h: calls.append(p) or False)
        # ``burn_password_check`` calls the module-level verify_password.
        with pytest.raises(InvalidCredentialsError):
            authenticate(db, "nadie", "x")
        assert calls == ["x"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _claims(role: str = "MEDICO", **overrides) -> dict:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(uuid.uuid4()),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": security.settings.jwt_issuer,
        "aud": security.settings.jwt_audience,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _sign(claims: dict, key: str | None = None, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key or security.settings.jwt_secret_key, algorithm=algorithm)


class TestTokens:
    def test_token_roundtrip(self):
        user_id = uuid.uuid4()
        token = security.create_access_token(user_id, "ADMIN")
        payload = security.decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["role"] == "ADMIN"

    def test_the_token_carries_only_the_id_the_role_and_the_registered_claims(self):
        payload = security.decode_access_token(security.create_access_token(uuid.uuid4(), "ADMIN"))
        assert set(payload) == {"sub", "role", "iat", "exp", "iss", "aud"}
        assert payload["iss"] == "saludred"
        assert payload["aud"] == "saludred-api"

    def test_the_default_expiry_is_fifteen_minutes(self, monkeypatch):
        monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
        assert Settings(_env_file=None).access_token_expire_minutes == 15
        payload = security.decode_access_token(security.create_access_token(uuid.uuid4(), "ADMIN"))
        assert payload["exp"] - payload["iat"] == security.settings.access_token_expire_minutes * 60

    def test_the_issuer_and_audience_are_configurable_with_defaults(self, monkeypatch):
        monkeypatch.delenv("JWT_ISSUER", raising=False)
        monkeypatch.delenv("JWT_AUDIENCE", raising=False)
        configured = Settings(_env_file=None)
        assert (configured.jwt_issuer, configured.jwt_audience) == ("saludred", "saludred-api")

    def test_garbage_token_is_rejected(self):
        assert security.decode_access_token("no.es.un.jwt") is None

    def test_expired_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(security.settings, "access_token_expire_minutes", -5)
        token = security.create_access_token(uuid.uuid4(), "ADMIN")
        assert security.decode_access_token(token) is None

    def test_a_role_changed_without_resigning_is_rejected(self):
        """The attacker edits the payload (MEDICO -> ADMIN) and keeps the old signature."""

        token = _sign(_claims("MEDICO"))
        header, payload, signature = token.split(".")
        edited = json.loads(_unb64url(payload))
        edited["role"] = "ADMIN"
        forged = ".".join([header, _b64url(json.dumps(edited).encode()), signature])
        assert security.decode_access_token(token) is not None
        assert security.decode_access_token(forged) is None

    def test_a_role_changed_and_signed_with_another_key_is_rejected(self):
        forged = _sign(_claims("ADMIN"), key="una-clave-que-no-es-la-del-servidor")
        assert security.decode_access_token(forged) is None

    def test_alg_none_is_rejected(self):
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(
            json.dumps(
                {**_claims("ADMIN"), "iat": 1_900_000_000, "exp": 4_100_000_000}, default=str
            ).encode()
        )
        for forged in (f"{header}.{payload}.", f"{header}.{payload}"):
            assert security.decode_access_token(forged) is None

    def test_another_algorithm_is_rejected_even_with_the_right_key(self):
        assert security.decode_access_token(_sign(_claims(), algorithm="HS512")) is None
        assert security.decode_access_token(_sign(_claims(), algorithm="HS256")) is not None

    def test_wrong_audience_is_rejected(self):
        assert security.decode_access_token(_sign(_claims(aud="saludred-interop"))) is None

    def test_wrong_issuer_is_rejected(self):
        assert security.decode_access_token(_sign(_claims(iss="otro-emisor"))) is None

    @pytest.mark.parametrize("missing", ["sub", "role", "iat", "exp", "iss", "aud"])
    def test_every_required_claim_must_be_present(self, missing: str):
        claims = _claims()
        claims.pop(missing)
        assert security.decode_access_token(_sign(claims)) is None
