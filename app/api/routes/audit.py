"""Audit log endpoints.

The audit trail is only evidence if somebody can read it: this endpoint is what
turns ``audit_log`` from a private table into a verifiable record. ADMIN only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.core.net import client_ip
from app.models.enums import AuditAction
from app.models.governance import AuditLog
from app.schemas.audit import AuditChainHead, AuditChainReport, AuditLogRead
from app.schemas.common import Page
from app.services import audit_chain, trail
from app.services.errors import commit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/audit-logs", tags=["auditoria"])


TAIL_NOTE = (
    "Limite: borrar las ultimas filas de la cadena no se detecta desde la base de datos. "
    "Publique el ultimo hash cada dia fuera de ella y compare."
)


@router.get(
    "/verify",
    response_model=AuditChainReport,
    summary="Verificar la cadena de hashes de la auditoria (solo Admin)",
)
def verify_audit_chain(db: DbSession, user: CurrentUser, request: Request) -> AuditChainReport:
    authz.require_admin(user)
    report = audit_chain.verify(db)
    # The verification is itself audited. It is written AFTER the walk, so it never
    # changes what was just verified; it becomes the next link of the chain.
    trail.audit(
        db,
        action=AuditAction.AUDIT_CHAIN_VERIFIED,
        entity_type="audit_log",
        entity_id=None,
        actor=user,
        metadata={
            "ok": report.ok,
            "filas_verificadas": report.verified,
            "primera_fila_rota_seq": report.first_broken_seq,
            "motivo": report.reason,
            "filas_sin_cadena_posteriores": report.unchained_after_start,
        },
        ip_address=client_ip(request),
    )
    commit(db)
    return AuditChainReport(
        ok=report.ok,
        filas_verificadas=report.verified,
        primera_fila_rota_id=report.first_broken_id,
        primera_fila_rota_seq=report.first_broken_seq,
        motivo=report.reason,
        ultimo_seq=report.last_seq,
        ultimo_hash=report.last_hash,
        inicio_cadena=report.chain_started_at,
        filas_sin_cadena_posteriores=report.unchained_after_start,
        verificado_en=datetime.now(timezone.utc),
        nota=TAIL_NOTE,
    )


@router.get(
    "/head",
    response_model=AuditChainHead,
    summary="Ultimo hash de la cadena, para publicarlo cada dia (solo Admin)",
)
def audit_chain_head(db: DbSession, user: CurrentUser) -> AuditChainHead:
    authz.require_admin(user)
    last = audit_chain.head(db)
    if last is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La cadena de auditoria todavia no tiene filas",
        )
    now = datetime.now(timezone.utc)
    logger.info("AUDIT_HEAD seq=%s hash=%s consultado=%s", last.seq, last.row_hash, now.isoformat())
    return AuditChainHead(
        seq=last.seq,
        ultimo_hash=last.row_hash,
        fecha=last.created_at,
        consultado_en=now,
        nota=TAIL_NOTE,
    )


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
