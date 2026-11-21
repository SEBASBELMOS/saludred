"""Consent lifecycle and the destination gate (R32, Ley 1581).

A consent binds one patient to exactly one destination — an in-network
``organizations`` row or a partner ``interop_clients`` row — for one validity
window. Rows are immutable, like referrals: renewal is a NEW row and revocation
stamps the existing one, so the full history survives for the audit trail.

The outbound service ``interop.envio.send_to_partner`` calls the reusable gate
after the patient scope check and before building or sending a partner Bundle.

Overlap between two live windows is enforced at the query level on purpose:
PostgreSQL cannot express interval non-overlap with a CHECK, so the service
serializes each patient/destination with a transaction advisory lock, loads
the candidate rows and rejects an overlapping half-open window with
``ConflictError``. The exactly-one-destination rule IS enforced by the database
CHECK (see the model and the migration).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.core import authz
from app.models.clinical import Encounter
from app.models.consent import Consent
from app.models.enums import AuditAction, ConsentStatus, RoleCode
from app.models.identity import User
from app.models.interop import InteropClient
from app.models.organization import Organization
from app.models.patient import Patient
from app.schemas.consent import ConsentRead
from app.services import patient_access, trail
from app.services import patients as patients_service
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidValueError,
    NotFoundError,
    commit,
)

ENTITY = "consents"
MAX_LIST = 200

# The destination of a consent, resolved to one identity: an in-network
# organization (by UUID) or a partner interop client (by business client_id,
# which the service resolves to the row). This is the exact contract E1 calls.
Destination = (
    tuple[Literal["organization"], uuid.UUID] | tuple[Literal["interop_client"], str]
)

FORBIDDEN_MESSAGE = (
    "No existe consentimiento valido para compartir datos del paciente con este destino"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock_destination(db: Session, patient_id: uuid.UUID, kind: str, value: uuid.UUID) -> None:
    """Serialize registration and revocation for one patient and destination."""
    if db.get_bind().dialect.name == "postgresql":
        identity = f"consents:{patient_id}:{kind}:{value}".encode()
        key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidValueError("Consent timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    """One comparable instant: SQLite returns naive datetimes (stored in UTC)."""

    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _windows_overlap(
    a_from: datetime,
    a_until: datetime | None,
    b_from: datetime,
    b_until: datetime | None,
) -> bool:
    """Half-open validity windows overlap; a NULL end means indefinite."""

    a_from = _naive_utc(a_from)
    b_from = _naive_utc(b_from)
    if a_until is not None:
        a_until = _naive_utc(a_until)
    if b_until is not None:
        b_until = _naive_utc(b_until)
    starts_before_b_ends = b_until is None or a_from < b_until
    starts_before_a_ends = a_until is None or b_from < a_until
    return starts_before_b_ends and starts_before_a_ends


def _audit_meta(
    consent: Consent, *, business_client_id: str | None = None
) -> dict[str, Any]:
    """The destination identity plus the validity window, for the audit trail."""

    if business_client_id is not None:
        interop_client_id = business_client_id
    elif consent.interop_client_id:
        interop_client_id = consent.interop_client.client_id
    else:
        interop_client_id = None
    return {
        "patient_id": str(consent.patient_id),
        "organization_id": (
            str(consent.organization_id) if consent.organization_id else None
        ),
        # The business client_id, not the row UUID: the partner identity.
        "interop_client_id": interop_client_id,
        "valid_from": consent.valid_from.isoformat(),
        "valid_until": consent.valid_until.isoformat() if consent.valid_until else None,
    }


# ---------------------------------------------------------------------------
# The gate (E1 contract)
# ---------------------------------------------------------------------------


def require_valid_consent(
    db: Session, *, patient_id: uuid.UUID, destination: Destination
) -> None:
    """Raise ``ForbiddenError`` unless a valid consent exists for this destination.

    Validity: the consent matches the patient AND the destination, it already
    started (``valid_from <= now``), it has not ended (``valid_until`` NULL or
    ``now < valid_until``) and it is not revoked. Missing, expired, revoked,
    not-yet-valid and wrong-destination consents all raise the same error, which
    the API maps to HTTP 403.
    """

    kind, value = destination
    now = _now()
    if kind == "organization":
        destination_column = Consent.organization_id
        destination_value = value
    elif kind == "interop_client":
        client_id = db.scalar(
            select(InteropClient.id).where(
                InteropClient.client_id == value, InteropClient.is_active.is_(True)
            )
        )
        if client_id is None:
            raise ForbiddenError(FORBIDDEN_MESSAGE)
        destination_column = Consent.interop_client_id
        destination_value = client_id
    else:
        raise InvalidValueError(
            f"Tipo de destino de consentimiento desconocido: {kind!r}"
        )

    valid = db.scalar(
        select(Consent.id)
        .where(
            Consent.patient_id == patient_id,
            destination_column == destination_value,
            Consent.revoked_at.is_(None),
            Consent.valid_from <= now,
            or_(Consent.valid_until.is_(None), Consent.valid_until > now),
        )
        .limit(1)
    )
    if valid is None:
        raise ForbiddenError(FORBIDDEN_MESSAGE)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_consent(
    db: Session,
    *,
    actor: User,
    patient_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
    interop_client_id: str | None = None,
    valid_from: datetime,
    valid_until: datetime | None = None,
) -> Consent:
    """Register a consent for one patient and exactly one destination.

    ``interop_client_id`` is the business client_id (resolved to the partner
    row); ``organization_id`` is the in-network organization UUID. Raises
    ``InvalidValueError`` for both/neither destination or a window that ends
    before it starts, ``NotFoundError`` for an unknown patient or destination,
    and ``ConflictError`` when the window overlaps an existing live consent
    (query-level enforcement — the database cannot express interval overlap).
    """

    authz.require_role(actor, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    if (organization_id is None) == (interop_client_id is None):
        raise InvalidValueError(
            "El consentimiento exige exactamente un destino "
            "(organizacion o cliente de interoperabilidad)"
        )
    valid_from = _aware_utc(valid_from)
    valid_until = _aware_utc(valid_until) if valid_until is not None else None
    if valid_until is not None and valid_until <= valid_from:
        raise InvalidValueError(
            "La vigencia del consentimiento termina antes de empezar"
        )

    patients_service.get_patient(db, patient_id)
    patient_access.ensure_staff_patient_scope(db, actor, patient_id)

    if organization_id is not None:
        if (
            db.scalar(
                select(Organization.id).where(
                    Organization.id == organization_id,
                    Organization.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise NotFoundError("Organizacion no encontrada")
        client: InteropClient | None = None
        destination_column = Consent.organization_id
        destination_value = organization_id
    else:
        client = db.scalar(
            select(InteropClient).where(InteropClient.client_id == interop_client_id)
        )
        if client is None:
            raise NotFoundError("Cliente de interoperabilidad no encontrado")
        if not client.is_active:
            raise ForbiddenError("Partner is inactive")
        destination_column = Consent.interop_client_id
        destination_value = client.id

    _lock_destination(db, patient_id, destination_column.key, destination_value)
    now = _now()
    live_candidates = db.scalars(
        select(Consent).where(
            Consent.patient_id == patient_id,
            destination_column == destination_value,
            Consent.revoked_at.is_(None),
            # Expired rows stay readable history and never block a renewal.
            or_(Consent.valid_until.is_(None), Consent.valid_until > now),
        )
    )
    for existing in live_candidates:
        if _windows_overlap(
            valid_from, valid_until, existing.valid_from, existing.valid_until
        ):
            raise ConflictError(
                "Ya existe un consentimiento vigente para este paciente y destino"
            )

    consent = Consent(
        patient_id=patient_id,
        organization_id=organization_id,
        interop_client_id=client.id if client is not None else None,
        valid_from=valid_from,
        valid_until=valid_until,
        created_at=_now(),
        created_by=actor.id,
    )
    db.add(consent)
    db.flush()
    trail.audit(
        db,
        action=AuditAction.CONSENT_CREATED,
        entity_type=ENTITY,
        entity_id=consent.id,
        actor=actor,
        metadata=_audit_meta(
            consent, business_client_id=client.client_id if client is not None else None
        ),
    )
    commit(db)
    return consent


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def revoke_consent(db: Session, *, actor: User, consent_id: uuid.UUID) -> Consent:
    """Revoke all windows for the selected patient/destination, preserving history.

    The row stays readable for the audit trail (expired and revoked consents are
    history, not garbage). An unknown consent is ``NotFoundError``; revoking a
    consent that is already revoked is ``ConflictError``.
    """

    authz.require_role(actor, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    consent = db.scalar(
        select(Consent)
        .where(Consent.id == consent_id)
        .execution_options(populate_existing=True)
    )
    if consent is None:
        raise NotFoundError("Consentimiento no encontrado")
    patient_access.ensure_staff_patient_scope(db, actor, consent.patient_id)
    destination_column = (
        Consent.organization_id if consent.organization_id is not None else Consent.interop_client_id
    )
    destination_value = consent.organization_id or consent.interop_client_id
    # Take the destination lock before row locks, in the same order as registration.
    _lock_destination(db, consent.patient_id, destination_column.key, destination_value)
    rows = list(db.scalars(
        select(Consent).where(
            Consent.patient_id == consent.patient_id,
            destination_column == destination_value,
        ).order_by(Consent.id).with_for_update().execution_options(populate_existing=True)
    ))
    if consent.revoked_at is not None:
        raise ConflictError("El consentimiento ya esta revocado")

    revoked_at = _now()
    for row in rows:
        if row.revoked_at is not None:
            continue
        row.revoked_at = revoked_at
        row.revoked_by = actor.id
        trail.audit(
            db, action=AuditAction.CONSENT_REVOKED, entity_type=ENTITY,
            entity_id=row.id, actor=actor, metadata=_audit_meta(row),
        )
    commit(db)
    return consent


# ---------------------------------------------------------------------------
# Listing and status
# ---------------------------------------------------------------------------


def list_consents(
    db: Session, user: User, *, paciente_id: uuid.UUID | None = None, limit: int = MAX_LIST
) -> list[Consent]:
    """Consents visible to an admin or medico, optionally for one patient.

    A soft-deleted patient's consents leave the list (the same convention as the
    referral list: this is the clinical surface). The evidence does not vanish —
    the rows and their CONSENT_CREATED/CONSENT_REVOKED audit entries stay.
    """

    authz.require_role(user, RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
    stmt = (
        select(Consent)
        .options(selectinload(Consent.interop_client))
        .join(Patient, Patient.id == Consent.patient_id)
        .where(Patient.deleted_at.is_(None))
    )
    if user.role.code in authz.IPS_SCOPED_ROLES:
        scope = authz.org_filter_for(user)
        if scope is None:
            return []
        stmt = stmt.where(Consent.patient_id.in_(
            select(Encounter.patient_id).where(
                Encounter.organization_id == scope, Encounter.deleted_at.is_(None)
            )
        ))
    if paciente_id is not None:
        stmt = stmt.where(Consent.patient_id == paciente_id)
    return list(
        db.scalars(
            stmt.order_by(Consent.created_at.desc(), Consent.id).limit(max(1, min(limit, MAX_LIST)))
        ).unique()
    )


def status_of(consent: Consent, *, now: datetime | None = None) -> ConsentStatus:
    """The public status DERIVED from the stored facts (never stored itself).

    A revoked consent is ``revocada`` whatever its window says; a window that
    ended is ``expirada``; a window that has not started yet is ``programada``
    (it authorizes nothing yet, and it is not expired either); anything else is
    ``activa``.
    """

    moment = _naive_utc(now or _now())
    if consent.revoked_at is not None:
        return ConsentStatus.REVOKED
    if consent.valid_until is not None and moment >= _naive_utc(consent.valid_until):
        return ConsentStatus.EXPIRED
    if moment < _naive_utc(consent.valid_from):
        return ConsentStatus.SCHEDULED
    return ConsentStatus.ACTIVE


def to_read(consent: Consent) -> ConsentRead:
    """The public DTO: destination, DERIVED status and the lifecycle stamps.

    ``cliente_interop_id`` is the business client_id (the partner identity the
    API contract speaks in), not the row UUID. The relationship is resolved
    here, while the session is still open, so serialization never touches the
    database.
    """

    return ConsentRead(
        id=consent.id,
        paciente_id=consent.patient_id,
        organizacion_id=consent.organization_id,
        cliente_interop_id=(
            consent.interop_client.client_id if consent.interop_client is not None else None
        ),
        estado=status_of(consent),
        vigente_desde=consent.valid_from,
        vigente_hasta=consent.valid_until,
        creado_en=consent.created_at,
        creado_por=consent.created_by,
        revocado_en=consent.revoked_at,
        revocado_por=consent.revoked_by,
    )
