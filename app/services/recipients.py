"""Who answers for a patient: the recipients of reactive events.

Order of preference:

1. the patient's responsible clinician, when set and still an active account;
2. the medico/especialista accounts of the IPS of the patient's most recent
   encounter;
3. the admins, so an event is never addressed to nobody.

The statements are built by small functions so tests can compile them for
PostgreSQL without a server.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.clinical import Encounter
from app.models.enums import RoleCode
from app.models.identity import Role, User
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.referral import ACCESS_GRANTING_STATES, Referral

CLINICIAN_ROLES = (RoleCode.IPS_CLINICAL_OPERATOR, RoleCode.SPECIALIST)


def latest_encounter_organization_stmt(patient_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return (
        select(Encounter.organization_id)
        .where(Encounter.patient_id == patient_id, Encounter.deleted_at.is_(None))
        .order_by(Encounter.started_at.desc(), Encounter.id)
        .limit(1)
    )


def _usable_users_stmt(roles: tuple[RoleCode, ...]) -> Select[tuple[uuid.UUID]]:
    return (
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            Role.code.in_(roles),
            User.is_active.is_(True),
            User.locked_at.is_(None),
        )
        .order_by(User.username)
    )


def clinicians_of_organization_stmt(organization_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return _usable_users_stmt(CLINICIAN_ROLES).where(User.organization_id == organization_id)


def patient_encounter_organizations_stmt(patient_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """IPS where the patient has at least one live encounter."""

    return (
        select(Encounter.organization_id)
        .where(Encounter.patient_id == patient_id, Encounter.deleted_at.is_(None))
        .distinct()
    )


def eligible_clinicians_stmt(
    organization_ids: tuple[uuid.UUID, ...],
) -> Select[tuple[User, Organization]]:
    """Active medico/especialista accounts of the given IPS, with their IPS."""

    return (
        select(User, Organization)
        .join(Role, Role.id == User.role_id)
        .join(Organization, Organization.id == User.organization_id)
        .where(
            Role.code.in_(CLINICIAN_ROLES),
            User.is_active.is_(True),
            User.locked_at.is_(None),
            User.organization_id.in_(organization_ids),
        )
        .order_by(Organization.name, User.full_name, User.id)
    )


def patient_audience_stmt(patient_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Clinicians whose work tray can contain something about the patient.

    Active medico/especialista accounts of the IPS where the patient has live
    encounters, plus the specialists holding a referral grant for the patient.
    It is the audience of the pending-work counters and of the critical alert.
    """

    in_ips = _usable_users_stmt(CLINICIAN_ROLES).where(
        User.organization_id.in_(patient_encounter_organizations_stmt(patient_id))
    )
    referred = (
        select(Referral.specialist_user_id)
        .join(User, User.id == Referral.specialist_user_id)
        .where(
            Referral.patient_id == patient_id,
            Referral.status.in_(ACCESS_GRANTING_STATES),
            User.is_active.is_(True),
            User.locked_at.is_(None),
        )
    )
    return in_ips.order_by(None).union(referred)


def audience_for_patient(db: Session, patient_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    return tuple(db.scalars(patient_audience_stmt(patient_id)))


def coordinators_stmt() -> Select[tuple[uuid.UUID]]:
    return _usable_users_stmt((RoleCode.EPS_COORDINATOR,))


def operators_of_organization_stmt(organization_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """The medicos (IPS clinical operators) of one IPS."""

    return _usable_users_stmt((RoleCode.IPS_CLINICAL_OPERATOR,)).where(
        User.organization_id == organization_id
    )


def accountants_of_eps_stmt(eps_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    """Active accountants whose account belongs to the given EPS."""

    return _usable_users_stmt((RoleCode.ACCOUNTANT,)).where(User.organization_id == eps_id)


def admins_stmt() -> Select[tuple[uuid.UUID]]:
    return _usable_users_stmt((RoleCode.ADMIN,))


def is_usable_clinician(db: Session, user_id: uuid.UUID) -> bool:
    stmt = _usable_users_stmt(CLINICIAN_ROLES).where(User.id == user_id)
    return db.scalar(stmt.limit(1)) is not None


def for_billing(db: Session, eps_id: uuid.UUID | None) -> tuple[uuid.UUID, ...]:
    """Recipients of a billing event: the accountants of the patient's EPS, plus
    the admins. Never the medico, the especialista or the patient."""

    accountants = tuple(db.scalars(accountants_of_eps_stmt(eps_id))) if eps_id else ()
    return accountants + tuple(db.scalars(admins_stmt()))


def for_patient(db: Session, patient: Patient) -> tuple[uuid.UUID, ...]:
    """Recipients of a clinical event about ``patient``."""

    if patient.responsible_clinician_id is not None and is_usable_clinician(
        db, patient.responsible_clinician_id
    ):
        return (patient.responsible_clinician_id,)

    organization_id = db.scalar(latest_encounter_organization_stmt(patient.id))
    if organization_id is not None:
        clinicians = tuple(db.scalars(clinicians_of_organization_stmt(organization_id)))
        if clinicians:
            return clinicians

    return tuple(db.scalars(admins_stmt()))
