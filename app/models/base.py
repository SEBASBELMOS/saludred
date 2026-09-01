"""Declarative base and the column mixins shared across the model.

The soft-delete contract of this project is a single rule: ``deleted_at IS NULL``
means the row is live. There is no parallel ``is_active`` flag duplicating that
state, because two columns describing one fact eventually disagree.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Deterministic constraint names. Without this, PostgreSQL invents names such as
# ``patients_created_by_fkey`` and Alembic cannot write a reliable downgrade,
# because dropping a constraint requires knowing what it is called.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for every mapped entity."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _actor_fk() -> ForeignKey:
    """Foreign key to ``users``, added after every table exists.

    ``organizations`` needs ``users`` for authorship while ``users`` needs
    ``organizations`` for scope: a plain circular reference that PostgreSQL
    cannot satisfy in either creation order. ``use_alter`` breaks the cycle by
    emitting these constraints as ALTER TABLE statements once all tables are
    created.
    """

    return ForeignKey("users.id", ondelete="RESTRICT", use_alter=True)


class UUIDPrimaryKeyMixin:
    """UUID surrogate key.

    UUIDs are used instead of sequential integers because rows are exported to an
    external FHIR server: a globally unique key can be reused as the business
    identifier without leaking row counts.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuthorshipMixin:
    """Ownership of a row.

    ``created_by`` is not bookkeeping: the access rules let a clinical operator
    soft-delete only the records they authored, and that rule is unenforceable
    without this column.
    """

    @declared_attr
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid(as_uuid=True), _actor_fk(), nullable=True)

    @declared_attr
    def updated_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid(as_uuid=True), _actor_fk(), nullable=True)


class SoftDeleteMixin:
    """Logical deletion with a restoration trail.

    ``restored_at`` / ``restored_by`` are kept separate from the deletion columns
    so an undelete never erases the evidence of who deleted the row first.
    """

    @declared_attr
    def deleted_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True), nullable=True, index=True)

    @declared_attr
    def deleted_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid(as_uuid=True), _actor_fk(), nullable=True)

    @declared_attr
    def restored_at(cls) -> Mapped[datetime | None]:
        return mapped_column(DateTime(timezone=True), nullable=True)

    @declared_attr
    def restored_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(Uuid(as_uuid=True), _actor_fk(), nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
