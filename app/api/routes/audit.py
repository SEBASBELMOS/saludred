"""Audit log endpoints.

The audit trail is only evidence if somebody can read it: this endpoint is what
turns ``audit_log`` from a private table into a verifiable record. ADMIN only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.enums import AuditAction
from app.models.governance import AuditLog
from app.schemas.audit import AuditLogRead
from app.schemas.common import Page

router = APIRouter(prefix="/api/v1/audit-logs", tags=["auditoria"])


@router.get(
    "",
    response_model=Page[AuditLogRead],
    summary="Consultar bitacora de auditoria (solo Admin)",
)
def list_audit_logs(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    action: AuditAction | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    entity_id: uuid.UUID | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
) -> Page[AuditLogRead]:
    authz.require_admin(user)

    stmt = select(AuditLog).options(joinedload(AuditLog.user))
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    entries = db.scalars(
        stmt.order_by(AuditLog.created_at.desc())
        .offset((params.page - 1) * params.page_size)
        .limit(params.page_size)
    )
    items = [
        AuditLogRead(
            id=entry.id,
            user_id=entry.user_id,
            username=entry.user.username if entry.user is not None else None,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            metadata_json=entry.metadata_json,
            created_at=entry.created_at,
        )
        for entry in entries
    ]
    return Page(
        items=items, total=total, page=params.page, page_size=params.page_size
    )
