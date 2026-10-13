"""Who may touch the clinical data of one patient.

Patients do not belong to an IPS; their care does, through encounters. An
institution-bound clinician (operator or specialist) therefore reaches a patient
only when the patient has at least one live encounter in that clinician's IPS.
Admin and the network coordinator see the whole network. The PATIENT role sees
only itself.

**Referral grant (R08).** A specialist who holds a pending, accepted or
completed referral for a patient may read that patient's record even outside
their IPS: the whole point of a referral is that the specialist sees what the
referring doctor sees. A rejected referral grants nothing, and neither does a
referral addressed to somebody else. The grant lives HERE, in the one place
where institutional scope is decided, so every read path (patient, encounters,
observations, images, AI reports, clinical record, FHIR bundle) applies it the
same way.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.orm import Session

from app.core import authz
from app.models.clinical import Encounter
from app.models.enums import RoleCode
from app.models.identity import User
from app.models.referral import ACCESS_GRANTING_STATES, Referral
from app.services.errors import ForbiddenError


def has_encounter_in(db: Session, patient_id: uuid.UUID, organization_id: uuid.UUID | None) -> bool:
    if organization_id is None:
        return False
    return (
        db.scalar(
            select(Encounter.id)
            .where(
                Encounter.patient_id == patient_id,
                Encounter.organization_id == organization_id,
                Encounter.deleted_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


# ---------------------------------------------------------------------------
# Referral grant
# ---------------------------------------------------------------------------


def referred_patient_ids(user: User) -> Select[tuple[uuid.UUID]] | None:
    """Patients the user may read through a referral, as a subquery.

    ``None`` when the user cannot hold such a grant (anybody who is not an
    institution-bound specialist), so callers skip the extra condition.
    """

    if user.role.code != RoleCode.SPECIALIST:
        return None
    return select(Referral.patient_id).where(
        Referral.specialist_user_id == user.id,
        Referral.status.in_(ACCESS_GRANTING_STATES),
    )


def has_referral_access(db: Session, user: User, patient_id: uuid.UUID) -> bool:
    granted = referred_patient_ids(user)
    if granted is None:
        return False
    return db.scalar(granted.where(Referral.patient_id == patient_id).limit(1)) is not None


def scope_clause(
    user: User, *, organization_column: ColumnElement, patient_column: ColumnElement
) -> ColumnElement[bool] | None:
    """The institutional filter of a list query, referral grant included.

    ``None`` means "no restriction" (admin, coordinator). For the IPS-bound
    roles it is "the row belongs to my IPS", plus, for a specialist, "or it is
    about a patient I was referred".
    """

    scope = authz.org_filter_for(user)
    if scope is None:
        return None
    clause: ColumnElement[bool] = organization_column == scope
    granted = referred_patient_ids(user)
    if granted is not None:
        clause = or_(clause, patient_column.in_(granted))
    return clause


def org_scope_for_patient(db: Session, user: User, patient_id: uuid.UUID) -> uuid.UUID | None:
    """Organization filter for reading ONE patient: none when a referral grants it."""

    if has_referral_access(db, user, patient_id):
        return None
    return authz.org_filter_for(user)


def ensure_scope_or_referral(
    db: Session, user: User, organization_id: uuid.UUID | None, patient_id: uuid.UUID
) -> None:
    """``authz.ensure_org_scope`` for a READ, honouring the referral grant."""

    try:
        authz.ensure_org_scope(user, organization_id)
    except ForbiddenError:
        if has_referral_access(db, user, patient_id):
            return
        raise


# ---------------------------------------------------------------------------
# Patient-level checks
# ---------------------------------------------------------------------------


def ensure_staff_patient_scope(db: Session, user: User, patient_id: uuid.UUID) -> None:
    """Institutional boundary for a staff user over one patient."""

    if user.role.code in authz.IPS_SCOPED_ROLES and not (
        has_encounter_in(db, patient_id, user.organization_id)
        or has_referral_access(db, user, patient_id)
    ):
        raise ForbiddenError("El paciente no tiene atencion en su institucion")


def ensure_record_access(db: Session, user: User, patient_id: uuid.UUID) -> None:
    """Staff within scope, or the patient reading its own record."""

    if user.role.code == RoleCode.PATIENT:
        if user.patient_id != patient_id:
            raise ForbiddenError("Solo puede consultar su propia historia clinica")
        return
    authz.require_staff(user)
    ensure_staff_patient_scope(db, user, patient_id)
