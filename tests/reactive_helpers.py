"""Shared scaffolding for the Epic 7 tests (referrals, reactive flows, alerts)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import AuditLog, ImagingStudy, Notification, Organization, User
from app.models.enums import (
    AuditAction,
    ImagingModality,
    ImagingStudyStatus,
    RoleCode,
)
from app.models.identity import Role

REFERRALS = "/api/v1/referrals"
ALERTS = "/api/v1/alerts"
PENDING = "/api/v1/pendientes"


def add_specialist(
    db: Session,
    *,
    username: str,
    org_code: str = "IPS-SUR",
    active: bool = True,
    locked: bool = False,
) -> User:
    """A specialist of another IPS: the seed only has one, in IPS-NORTE."""

    role = db.scalar(select(Role).where(Role.code == RoleCode.SPECIALIST))
    org = db.scalar(select(Organization).where(Organization.code == org_code))
    user = User(
        username=username,
        full_name=f"Especialista {username}",
        email=f"{username}@saludred.local",
        password_hash=hash_password("Demo2026!"),
        role_id=role.id,
        organization_id=org.id,
        is_active=active,
        locked_at=datetime.now(timezone.utc) if locked else None,
    )
    db.add(user)
    db.commit()
    return user


def inbox(db: Session, username: str, tipo: str | None = None) -> list[Notification]:
    stmt = select(Notification).join(User, User.id == Notification.user_id).where(
        User.username == username
    )
    if tipo is not None:
        stmt = stmt.where(Notification.kind == tipo)
    return list(db.scalars(stmt.order_by(Notification.created_at, Notification.id)))


def audit_rows(db: Session, action: AuditAction) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action)))


def add_study(db: Session, patient_id: str, organization_id: uuid.UUID) -> ImagingStudy:
    """An imaging study registered at ``organization_id`` (pixels are not needed)."""

    study = ImagingStudy(
        patient_id=uuid.UUID(patient_id),
        organization_id=organization_id,
        study_instance_uid="1.2.840.99999.1.2.3",
        accession_number=f"ACC-{uuid.uuid4().hex[:8]}",
        modality=ImagingModality.DX,
        status=ImagingStudyStatus.REGISTERED,
        body_site="Torax",
        series_count=1,
        instance_count=1,
        started_at=datetime.now(timezone.utc),
    )
    db.add(study)
    db.commit()
    return study


def refer(client, patient_id: str, specialist: User, motivo: str = "Valoracion cardiologica"):  # noqa: ANN001
    return client.post(
        REFERRALS,
        json={
            "paciente_id": patient_id,
            "especialista_id": str(specialist.id),
            "motivo": motivo,
        },
    )


class Actor:
    """A client that acts as ONE account on every call.

    ``client_as`` installs a single global dependency override, so two clients
    held at the same time would both act as the last one built. Tests that talk
    as several users in turn use this wrapper, which re-installs its own user
    before each request.
    """

    def __init__(self, client_as, username: str) -> None:  # noqa: ANN001
        self._client_as = client_as
        self.username = username

    def _call(self, method: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return getattr(self._client_as(self.username), method)(*args, **kwargs)

    def get(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._call("get", *args, **kwargs)

    def post(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._call("post", *args, **kwargs)

    def put(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._call("put", *args, **kwargs)

    def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._call("delete", *args, **kwargs)


def actors(client_as):  # noqa: ANN001, ANN201
    """``actors(client_as)("operador.norte")`` -> an :class:`Actor`."""

    return lambda username: Actor(client_as, username)


class DownCollection:
    """A Mongo that does not answer: every operation times out."""

    def __getattr__(self, name: str):  # noqa: ANN204
        def fail(*_args: object, **_kwargs: object) -> None:
            from pymongo.errors import ServerSelectionTimeoutError

            raise ServerSelectionTimeoutError("test: mongo down")

        return fail
