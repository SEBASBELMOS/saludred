"""Authorization rules.

Authorization here is the combination of three questions, asked in order:

1. **Role** -- may this kind of user perform this kind of operation?
2. **Institution** -- is the record inside the organization the user belongs to?
3. **Ownership** -- did this user create the specific record?

Every rule raises ``ForbiddenError`` (HTTP 403) on failure and returns nothing
on success, so call sites read as declarations: ``require_role(user, ADMIN)``.

The rules take the ``User`` loaded from the database on this request, never the
JWT claims: a token proves identity, but permissions are always re-read from
their source of truth.
"""

from __future__ import annotations

import uuid

from app.models.enums import RoleCode
from app.models.identity import User
from app.services.errors import ForbiddenError

STAFF_ROLES = (
    RoleCode.ADMIN,
    RoleCode.EPS_COORDINATOR,
    RoleCode.IPS_CLINICAL_OPERATOR,
)


def require_role(user: User, *allowed: RoleCode) -> None:
    """Gate an operation to specific roles."""

    if user.role.code not in allowed:
        raise ForbiddenError(
            "El rol "
            f"{user.role.code.value} no esta autorizado para esta operacion"
        )


def require_staff(user: User) -> None:
    """Gate an operation to institutional roles, excluding patients.

    A patient account reads its own record through the ``/me`` endpoints; the
    administrative surface of the API is not part of its contract.
    """

    require_role(user, *STAFF_ROLES)


def require_admin(user: User) -> None:
    require_role(user, RoleCode.ADMIN)


def ensure_org_scope(user: User, organization_id: uuid.UUID | None) -> None:
    """Second question: institutional boundary.

    ADMIN and EPS_COORDINATOR see the whole network by definition of their
    roles. An IPS_CLINICAL_OPERATOR must stay inside its own institution --
    having permission to *edit encounters* never implies permission to edit
    encounters of another IPS.
    """

    if user.role.code in (RoleCode.ADMIN, RoleCode.EPS_COORDINATOR):
        return
    if user.organization_id is None or user.organization_id != organization_id:
        raise ForbiddenError("El registro pertenece a otra institucion de la red")


def ensure_owner_or_admin(user: User, created_by: uuid.UUID | None) -> None:
    """Third question: ownership.

    The clinical role may modify or logically delete only the records it
    authored. ADMIN is exempt. A record with no recorded author predates
    authorship tracking and is therefore admin-only.
    """

    if user.role.code == RoleCode.ADMIN:
        return
    if created_by is None or created_by != user.id:
        raise ForbiddenError(
            "Solo el autor del registro o un administrador puede modificarlo"
        )


def org_filter_for(user: User) -> uuid.UUID | None:
    """Organization filter to apply to list queries.

    ``None`` means "no restriction" (network-wide visibility). For the clinical
    operator the filter is its own IPS: the restriction is applied inside the
    query, so a scoped user cannot even enumerate what it may not read.
    """

    if user.role.code == RoleCode.IPS_CLINICAL_OPERATOR:
        return user.organization_id
    return None
