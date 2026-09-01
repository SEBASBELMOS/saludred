"""Domain errors raised by services and mapped to HTTP responses."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class ServiceError(Exception):
    """Base class for errors originating in the service layer."""


class NotFoundError(ServiceError):
    """The requested entity does not exist or has been soft-deleted."""


class ConflictError(ServiceError):
    """The request violates a uniqueness, reference or state constraint."""


def commit(db: Session) -> None:
    """Commit, translating database-level integrity violations into 409s.

    Every mutation goes through this helper so that a constraint fired by the
    database (duplicate document, broken reference) surfaces as a coherent
    ``ConflictError`` instead of a bare 500.
    """

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "La operacion viola una restriccion de integridad (duplicado o referencia inexistente)"
        ) from exc
