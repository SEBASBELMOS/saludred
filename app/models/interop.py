"""Interoperability between teams: registered partner systems and seen nonces.

``InteropClient`` is the identity of a partner SYSTEM (OAuth2 client credentials),
as ``User`` is the identity of a person. ``InteropNonce`` is the memory that makes a
signed message usable only once inside its time window.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.governance import JSON_COLUMN


class InteropClient(UUIDPrimaryKeyMixin, Base):
    """One partner system. One client per partner (slide 28).

    * ``secret_hash``: Argon2id hash of the ``client_secret`` (the same hasher as the
      passwords). It authenticates the partner at ``POST /oauth/token``.
    * ``scopes``: what the partner may do, e.g. ``system/Observation.write``. A token
      never carries more than this, however much the partner asks for.
    * ``signing_secret``: the secret of the per-message HMAC signature. It is DIFFERENT
      from the client secret and it cannot be hashed (the receiver recomputes the HMAC
      with it), so it is stored encrypted with the field-encryption keys
      (``v1.<kid>.<nonce>.<ciphertext>``, AAD bound to the client id).
    """

    __tablename__ = "interop_clients"

    client_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON_COLUMN, nullable=False)
    signing_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Names an environment prefix, never credentials or a caller-supplied URL.
    outbound_env_prefix: Mapped[str | None] = mapped_column(String(80), nullable=True)

    def __repr__(self) -> str:
        return f"<InteropClient {self.client_id}>"


class InteropNonce(UUIDPrimaryKeyMixin, Base):
    """A nonce already seen from a client. UNIQUE (client, nonce) is the replay check."""

    __tablename__ = "interop_nonces"
    __table_args__ = (
        UniqueConstraint("client_id", "nonce", name="uq_interop_nonces_client_id_nonce"),
    )

    client_id: Mapped[str] = mapped_column(String(80), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return f"<InteropNonce {self.client_id}:{self.nonce}>"


__all__ = ["InteropClient", "InteropNonce"]
