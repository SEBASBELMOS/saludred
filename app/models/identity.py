"""Authentication and authorization: roles and user accounts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RoleCode, enum_column

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.patient import Patient


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One of the four roles of the system.

    Roles live in a table rather than a column so the model stays relational and
    a role can gain attributes later without touching every user row.
    """

    __tablename__ = "roles"

    code: Mapped[RoleCode] = mapped_column(
        enum_column(RoleCode, "role_code"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role {self.code}>"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account that authenticates against the API.

    Two nullable foreign keys define the authorization scope:

    ``organization_id`` restricts an IPS_CLINICAL_OPERATOR to its own institution,
    and ``patient_id`` binds a PATIENT account to the single clinical record it is
    allowed to read. Both are enforced in ``app.core.authz``; a database CHECK
    cannot express them because the role code lives in another table.

    ``is_active`` here means "the account is enabled" and is not a soft delete.
    It is the one legitimate boolean flag in the schema.
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    role: Mapped["Role"] = relationship(back_populates="users")
    organization: Mapped["Organization | None"] = relationship(
        back_populates="users", foreign_keys=[organization_id]
    )
    patient: Mapped["Patient | None"] = relationship(
        back_populates="user_account", foreign_keys=[patient_id]
    )

    @property
    def role_code(self) -> RoleCode:
        return self.role.code

    def __repr__(self) -> str:
        return f"<User {self.username}>"
