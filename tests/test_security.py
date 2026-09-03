"""Password hashing and JWT lifecycle."""

from __future__ import annotations

import uuid

from app.core import security


class TestPasswords:
    def test_hash_roundtrip(self):
        digest = security.hash_password("Demo2026!")
        assert digest != "Demo2026!"
        assert security.verify_password("Demo2026!", digest)
        assert not security.verify_password("otra-clave", digest)


class TestTokens:
    def test_token_roundtrip(self):
        user_id = uuid.uuid4()
        token = security.create_access_token(user_id, "ADMIN")
        payload = security.decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == str(user_id)
        assert payload["role"] == "ADMIN"

    def test_garbage_token_is_rejected(self):
        assert security.decode_access_token("no.es.un.jwt") is None

    def test_expired_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(
            security.settings, "access_token_expire_minutes", -5
        )
        token = security.create_access_token(uuid.uuid4(), "ADMIN")
        assert security.decode_access_token(token) is None
