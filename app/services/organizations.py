"""CRUD operations for organizations (EPS and IPS)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import OrganizationType
from app.models.identity import User
from app.models.organization import Organization
from app.schemas.organization import OrganizationCreate, OrganizationUpdate
from app.services import soft_ops
from app.services.errors import ConflictError, NotFoundError, commit


def list_organizations(
    db: Session,
    *,
    page: int,
    page_size: int,
    organization_type: OrganizationType | None = None,
) -> tuple[list[Organization], int]:
    stmt = select(Organization).where(Organization.deleted_at.is_(None))
    if organization_type is not None:
        stmt = stmt.where(Organization.organization_type == organization_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(
        db.scalars(
            stmt.order_by(Organization.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def get_organization(db: Session, organization_id: uuid.UUID) -> Organization:
    organization = db.scalar(
        select(Organization).where(
            Organization.id == organization_id, Organization.deleted_at.is_(None)
        )
    )
    if organization is None:
        raise NotFoundError("Organizacion no encontrada")
    return organization


def list_ips_for_eps(db: Session, eps_id: uuid.UUID) -> list[Organization]:
    """Return the live IPS children of one EPS."""

    eps = get_organization(db, eps_id)
    if eps.organization_type != OrganizationType.EPS:
        raise ConflictError("La organizacion indicada no es una EPS")
    return list(
        db.scalars(
            select(Organization)
            .where(
                Organization.parent_organization_id == eps_id,
                Organization.organization_type == OrganizationType.IPS,
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.code)
        )
    )


def create_organization(
    db: Session, data: OrganizationCreate, *, actor: User
) -> Organization:
    _require_code_available(db, data.code)
    _validate_parent(db, data.organization_type, data.parent_organization_id)

    organization = Organization(**data.model_dump(), created_by=actor.id)
    db.add(organization)
    db.flush()
    soft_ops.audit_create(db, organization, actor=actor)
    commit(db)
    db.refresh(organization)
    return organization


def update_organization(
    db: Session, organization: Organization, data: OrganizationUpdate, *, actor: User
) -> Organization:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return organization

    if "code" in changes:
        _require_code_available(db, changes["code"], exclude_id=organization.id)

    merged_type = changes.get("organization_type", organization.organization_type)
    merged_parent = changes.get(
        "parent_organization_id", organization.parent_organization_id
    )
    if merged_parent == organization.id:
        raise ConflictError("Una organizacion no puede ser su propia EPS padre")

    if merged_type == OrganizationType.IPS:
        _validate_parent(db, merged_type, merged_parent)
        if organization.children and changes.get("organization_type") is not None:
            raise ConflictError(
                "No se puede convertir a IPS una organizacion con IPS hijas"
            )

    soft_ops.snapshot_before_update(
        db, organization, actor=actor, changed_fields=sorted(changes)
    )
    for field, value in changes.items():
        setattr(organization, field, value)
    organization.updated_by = actor.id

    commit(db)
    db.refresh(organization)
    return organization


def soft_delete_organization(
    db: Session, organization: Organization, *, actor: User
) -> None:
    live_children = db.scalar(
        select(func.count())
        .select_from(Organization)
        .where(
            Organization.parent_organization_id == organization.id,
            Organization.deleted_at.is_(None),
        )
    )
    if live_children:
        raise ConflictError(
            "No se puede eliminar la organizacion: tiene IPS asociadas activas"
        )

    soft_ops.soft_delete(db, organization, actor=actor)


def _require_code_available(
    db: Session, code: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(Organization.id).where(
        Organization.code == code, Organization.deleted_at.is_(None)
    )
    if exclude_id is not None:
        stmt = stmt.where(Organization.id != exclude_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise ConflictError("Ya existe una organizacion con ese codigo")


def _validate_parent(
    db: Session, organization_type: OrganizationType, parent_id: uuid.UUID | None
) -> None:
    if organization_type == OrganizationType.IPS:
        if parent_id is None:
            raise ConflictError("Una IPS debe referenciar una EPS padre")
        parent = db.scalar(
            select(Organization).where(
                Organization.id == parent_id, Organization.deleted_at.is_(None)
            )
        )
        if parent is None or parent.organization_type != OrganizationType.EPS:
            raise ConflictError("La organizacion padre debe ser una EPS activa")
    elif parent_id is not None:
        raise ConflictError("Una EPS no debe referenciar una organizacion padre")
