"""Chained audit: every row seals the previous one, and an admin can verify the chain.

Each audit row written after this feature stores::

    seq        position in the chain (1, 2, 3, ...), contiguous
    prev_hash  ``row_hash`` of the previous row (``GENESIS_HASH`` for the first)
    row_hash   SHA-256 of the canonical JSON of the row itself, including
               ``seq`` and ``prev_hash``

Editing a row changes its hash (the row no longer matches ``row_hash``); recomputing
that hash breaks the NEXT row, whose ``prev_hash`` still points at the old one;
deleting a row from the middle leaves a gap in ``seq`` and a broken link. Verification
walks the chain in order and reports the first row that fails.

What it does NOT detect: deleting the LAST rows of the chain (nothing after them points
at them) or rewriting the whole chain from some point on with every hash recomputed.
That is why the head (``seq`` + ``row_hash``) must be published outside the database
every day (``GET /api/v1/audit-logs/head``, ``scripts/publicar_hash_auditoria.py``):
a later verification can then compare the head it finds with the published one.

Rows written before the chain existed are not touched (``seq`` NULL): the chain starts
at the first new row. Writers are serialized with a PostgreSQL advisory lock held until
the end of the transaction, so two concurrent transactions can never both extend the
same head (a fork); SQLite (tests) has a single writer and needs no lock.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, lazyload

from app.models.governance import AuditLog

GENESIS_HASH = "0" * 64
# Arbitrary constant: the key of the advisory lock shared by every audit writer.
ADVISORY_LOCK_KEY = 7_352_001
_HEAD_KEY = "audit_chain_head"
VERIFY_BATCH = 1000

REASON_LINK = "enlace_roto"
REASON_CONTENT = "contenido_alterado"
REASON_MISSING = "fila_faltante"
# Rows newer than the start of the chain that carry no seq/hash: written behind the
# application's back (or by a path that skips the chain). The chain does not hold.
REASON_UNCHAINED = "filas_sin_cadena"


def _timestamp(value: datetime) -> str:
    """Canonical text of an instant, always UTC with microseconds."""

    if value.tzinfo is None:  # SQLite returns naive datetimes: they were written as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """The metadata as the JSON column will return it (tuples become lists, etc.)."""

    if metadata is None:
        return None
    return json.loads(json.dumps(metadata, default=str))


def canonical_json(
    *,
    entry_id: uuid.UUID,
    seq: int,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
    ip_address: str | None,
    created_at: datetime,
    prev_hash: str,
) -> str:
    return json.dumps(
        {
            "id": str(entry_id),
            "seq": seq,
            "usuario": str(user_id) if user_id else None,
            "accion": action,
            "entidad": entity_type,
            "entidad_id": str(entity_id) if entity_id else None,
            "metadatos": metadata,
            "ip": ip_address,
            "fecha": _timestamp(created_at),
            "anterior": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def hash_of(entry: AuditLog, *, seq: int, prev_hash: str) -> str:
    action = entry.action.value if hasattr(entry.action, "value") else str(entry.action)
    payload = canonical_json(
        entry_id=entry.id,
        seq=seq,
        user_id=entry.user_id,
        action=action,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        metadata=entry.metadata_json,
        ip_address=entry.ip_address,
        created_at=entry.created_at,
        prev_hash=prev_hash,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Writing (called only by ``trail.audit``)
# ---------------------------------------------------------------------------


def _lock_chain(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADVISORY_LOCK_KEY})


def _current_head(db: Session) -> tuple[int, str]:
    """``(seq, row_hash)`` of the last row, ``(0, GENESIS_HASH)`` when the chain is empty."""

    cached = db.info.get(_HEAD_KEY)
    root = db.get_transaction()
    if cached is not None and root is not None and cached[0] is root and cached[1] in db:
        return cached[1].seq, cached[1].row_hash
    # Rows added in this transaction but not flushed yet are invisible to a query
    # (the application session does not autoflush): look at them first.
    pending = [o for o in db.new if isinstance(o, AuditLog) and o.seq is not None]
    if pending:
        last = max(pending, key=lambda o: o.seq)
        return last.seq, last.row_hash
    row = db.execute(
        select(AuditLog.seq, AuditLog.row_hash)
        .where(AuditLog.seq.is_not(None))
        .order_by(AuditLog.seq.desc())
        .limit(1)
    ).first()
    return (row.seq, row.row_hash) if row else (0, GENESIS_HASH)


def seal(db: Session, entry: AuditLog) -> AuditLog:
    """Give ``entry`` its place in the chain and add it to the session.

    ``entry.created_at`` is fixed here, in Python, and is part of the hash: the
    database ``now()`` is the START of the transaction, which would repeat for every
    row of one transaction and could not be recomputed later.
    """

    _lock_chain(db)
    seq, prev_hash = _current_head(db)
    entry.id = entry.id or uuid.uuid4()
    entry.metadata_json = normalize_metadata(entry.metadata_json)
    entry.created_at = datetime.now(timezone.utc)
    entry.seq = seq + 1
    entry.prev_hash = prev_hash
    entry.row_hash = hash_of(entry, seq=entry.seq, prev_hash=prev_hash)
    db.add(entry)
    root = db.get_transaction()
    if root is not None:
        db.info[_HEAD_KEY] = (root, entry)
    return entry


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainHead:
    seq: int
    row_hash: str
    created_at: datetime


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    verified: int
    first_broken_id: uuid.UUID | None
    first_broken_seq: int | None
    reason: str | None
    last_seq: int | None
    last_hash: str | None
    chain_started_at: datetime | None
    unchained_after_start: int


def head(db: Session) -> ChainHead | None:
    row = db.execute(
        select(AuditLog.seq, AuditLog.row_hash, AuditLog.created_at)
        .where(AuditLog.seq.is_not(None))
        .order_by(AuditLog.seq.desc())
        .limit(1)
    ).first()
    return ChainHead(row.seq, row.row_hash, row.created_at) if row else None


def verify(db: Session, *, batch_size: int = VERIFY_BATCH) -> ChainReport:
    """Walk the chain in order and report the first row that does not hold."""

    expected_seq = 0
    expected_prev = GENESIS_HASH
    verified = 0
    started_at: datetime | None = None
    last_seq: int | None = None
    last_hash: str | None = None
    broken: tuple[uuid.UUID, int, str] | None = None

    after_seq = 0  # keyset pagination: the chain is read in order, batch by batch
    while broken is None:
        rows = list(
            db.scalars(
                select(AuditLog)
                .options(lazyload(AuditLog.user))
                .where(AuditLog.seq.is_not(None), AuditLog.seq > after_seq)
                .order_by(AuditLog.seq)
                .limit(batch_size)
                # Read what the database holds NOW, not what the session cached.
                .execution_options(populate_existing=True)
            )
        )
        if not rows:
            break
        after_seq = rows[-1].seq
        for row in rows:
            if started_at is None:
                started_at = row.created_at
            if row.seq != expected_seq + 1:
                broken = (row.id, row.seq, REASON_MISSING)
            elif row.prev_hash != expected_prev:
                broken = (row.id, row.seq, REASON_LINK)
            elif hash_of(row, seq=row.seq, prev_hash=row.prev_hash) != row.row_hash:
                broken = (row.id, row.seq, REASON_CONTENT)
            if broken is not None:
                break
            verified += 1
            expected_seq, expected_prev = row.seq, row.row_hash
            last_seq, last_hash = row.seq, row.row_hash

    unchained = 0
    if started_at is not None:
        unchained = (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.seq.is_(None), AuditLog.created_at > started_at)
            )
            or 0
        )
    # A row inserted after the chain began but outside it is tampering (or a bug):
    # the chain is not "ok" even though every linked row still verifies.
    reason = broken[2] if broken else (REASON_UNCHAINED if unchained > 0 else None)
    return ChainReport(
        ok=broken is None and unchained == 0,
        verified=verified,
        first_broken_id=broken[0] if broken else None,
        first_broken_seq=broken[1] if broken else None,
        reason=reason,
        last_seq=last_seq,
        last_hash=last_hash,
        chain_started_at=started_at,
        unchained_after_start=unchained,
    )
