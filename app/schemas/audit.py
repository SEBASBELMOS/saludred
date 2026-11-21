"""Pydantic models for the traceability endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AuditAction


class RecordVersionRead(BaseModel):
    """One preserved version of a record, as returned by history endpoints."""

    model_config = ConfigDict(from_attributes=True)

    version_number: int
    snapshot_json: dict[str, Any]
    changed_fields: list[str] | None
    changed_by: uuid.UUID | None
    changed_at: datetime


class AuditChainReport(BaseModel):
    """Result of ``GET /audit-logs/verify``: does the hash chain hold?"""

    ok: bool
    filas_verificadas: int
    # First row that fails (``None`` when the chain holds) and why:
    # ``contenido_alterado``, ``enlace_roto`` or ``fila_faltante``.
    primera_fila_rota_id: uuid.UUID | None
    primera_fila_rota_seq: int | None
    motivo: str | None
    ultimo_seq: int | None
    ultimo_hash: str | None
    inicio_cadena: datetime | None
    # Rows written after the chain started that carry no hash (suspicious).
    filas_sin_cadena_posteriores: int
    verificado_en: datetime
    nota: str


class AuditChainHead(BaseModel):
    """Last link of the chain: publish it outside the database every day."""

    seq: int
    ultimo_hash: str
    fecha: datetime
    consultado_en: datetime
    nota: str


class AuditLogRead(BaseModel):
    """One audit entry. ``username`` is resolved for readability in demos."""

    id: uuid.UUID
    user_id: uuid.UUID | None
    username: str | None
    action: AuditAction
    entity_type: str
    entity_id: uuid.UUID | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
