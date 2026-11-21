"""Chained audit: each row seals the previous one; an admin can verify the chain.

SQLite in memory. The PostgreSQL advisory lock is not exercised here (SQLite has a
single writer); what is tested is the chain itself: linking, verification, and what
each kind of tampering looks like.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import AuditLog, Base, Role, User
from app.models.enums import AuditAction, RoleCode
from app.services import audit_chain, trail


@pytest.fixture
def db() -> Session:
    # StaticPool: the endpoint tests run the route in another thread and must see
    # the same in-memory database.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def _write(db: Session, count: int, *, commit_each: bool = False) -> None:
    for number in range(count):
        trail.audit(
            db,
            action=AuditAction.CREATE,
            entity_type="patients",
            entity_id=uuid.uuid4(),
            actor=None,
            metadata={"n": number, "texto": "ñandú", "lista": [1, 2]},
            ip_address="10.0.0.1",
        )
        if commit_each:
            db.commit()
    db.commit()


def _rows(db: Session) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).order_by(AuditLog.seq).execution_options(populate_existing=True)))


def test_rows_in_one_transaction_form_a_chain(db: Session) -> None:
    _write(db, 5)  # one transaction: nothing is flushed between the audits
    rows = _rows(db)
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0].prev_hash == audit_chain.GENESIS_HASH
    for previous, current in zip(rows, rows[1:]):
        assert current.prev_hash == previous.row_hash
    assert len({r.row_hash for r in rows}) == 5


def test_rows_across_transactions_keep_chaining(db: Session) -> None:
    _write(db, 3, commit_each=True)
    _write(db, 2)
    rows = _rows(db)
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert all(rows[i].prev_hash == rows[i - 1].row_hash for i in range(1, 5))


def test_the_chain_verifies(db: Session) -> None:
    _write(db, 7)
    report = audit_chain.verify(db, batch_size=3)  # several batches
    assert report.ok is True
    assert report.verified == 7
    assert report.first_broken_seq is None and report.reason is None
    assert report.last_seq == 7
    assert report.last_hash == _rows(db)[-1].row_hash
    assert report.unchained_after_start == 0


def test_an_empty_chain_verifies_and_has_no_head(db: Session) -> None:
    report = audit_chain.verify(db)
    assert report.ok and report.verified == 0 and report.last_hash is None
    assert audit_chain.head(db) is None


def test_the_hash_is_deterministic_over_canonical_json() -> None:
    entry_id, when = uuid.UUID(int=1), datetime(2026, 11, 11, 12, 0, 0, 123456, tzinfo=timezone.utc)
    fields = dict(
        entry_id=entry_id, seq=1, user_id=None, action="CREATE", entity_type="patients",
        entity_id=None, ip_address=None, created_at=when, prev_hash=audit_chain.GENESIS_HASH,
    )
    a = audit_chain.canonical_json(metadata={"b": 1, "a": 2}, **fields)
    b = audit_chain.canonical_json(metadata={"a": 2, "b": 1}, **fields)
    assert a == b  # key order does not matter
    assert '"fecha":"2026-11-11T12:00:00.123456Z"' in a
    naive = audit_chain.canonical_json(metadata={"a": 2, "b": 1}, **{**fields, "created_at": when.replace(tzinfo=None)})
    assert naive == a  # SQLite hands datetimes back without tzinfo: same instant (UTC)


def test_editing_the_metadata_of_a_row_breaks_the_chain_at_that_row(db: Session) -> None:
    _write(db, 6)
    target = _rows(db)[2]  # seq 3
    db.execute(update(AuditLog).where(AuditLog.id == target.id).values(metadata_json={"n": 999}))
    db.commit()

    report = audit_chain.verify(db)
    assert report.ok is False
    assert report.first_broken_seq == 3 and report.first_broken_id == target.id
    assert report.reason == audit_chain.REASON_CONTENT
    assert report.verified == 2


def test_editing_the_action_or_the_user_is_detected_too(db: Session) -> None:
    _write(db, 4)
    row = _rows(db)[1]
    db.execute(update(AuditLog).where(AuditLog.id == row.id).values(action=AuditAction.SOFT_DELETE))
    db.commit()
    assert audit_chain.verify(db).first_broken_seq == 2


def test_recomputing_the_hash_of_the_edited_row_breaks_the_next_link(db: Session) -> None:
    """The attacker fixes the hash of the row he edited: the NEXT row still points at the old one."""

    _write(db, 5)
    target = _rows(db)[1]  # seq 2
    db.execute(update(AuditLog).where(AuditLog.id == target.id).values(metadata_json={"n": 999}))
    db.commit()
    edited = db.get(AuditLog, target.id)
    db.refresh(edited)
    fixed = audit_chain.hash_of(edited, seq=edited.seq, prev_hash=edited.prev_hash)
    db.execute(update(AuditLog).where(AuditLog.id == target.id).values(row_hash=fixed))
    db.commit()

    report = audit_chain.verify(db)
    assert report.ok is False
    assert report.first_broken_seq == 3
    assert report.reason == audit_chain.REASON_LINK


def test_deleting_a_row_from_the_middle_is_detected(db: Session) -> None:
    _write(db, 6)
    victim = _rows(db)[2]  # seq 3
    db.execute(delete(AuditLog).where(AuditLog.id == victim.id))
    db.commit()

    report = audit_chain.verify(db)
    assert report.ok is False
    assert report.first_broken_seq == 4  # the first row after the gap
    assert report.reason == audit_chain.REASON_MISSING
    assert report.verified == 2


def test_deleting_the_first_rows_is_detected(db: Session) -> None:
    _write(db, 4)
    first = _rows(db)[0]
    db.execute(delete(AuditLog).where(AuditLog.id == first.id))
    db.commit()
    assert audit_chain.verify(db).reason == audit_chain.REASON_MISSING


def test_deleting_the_tail_is_NOT_detected_which_is_why_the_head_is_published(db: Session) -> None:
    """The documented limit: nothing after the last rows points at them."""

    _write(db, 6)
    published = audit_chain.head(db)  # what would have been published yesterday
    assert published.seq == 6

    db.execute(delete(AuditLog).where(AuditLog.seq > 4))
    db.commit()

    report = audit_chain.verify(db)
    assert report.ok is True and report.verified == 4  # the chain alone cannot tell
    now = audit_chain.head(db)
    # Comparing with the published head DOES: its hash is gone from the database.
    assert now.seq < published.seq
    assert now.row_hash != published.row_hash


def test_old_rows_without_a_chain_are_left_alone(db: Session) -> None:
    """The chain starts at the first new row; the previous ones keep NULL columns."""

    old = AuditLog(
        user_id=None, action=AuditAction.LOGIN, entity_type="users", entity_id=None,
        metadata_json=None, created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db.add(old)
    db.commit()

    _write(db, 3)

    assert (old.seq, old.prev_hash, old.row_hash) == (None, None, None)
    report = audit_chain.verify(db)
    assert report.ok and report.verified == 3 and report.unchained_after_start == 0
    first = next(r for r in _rows(db) if r.seq is not None)
    assert first.seq == 1 and first.prev_hash == audit_chain.GENESIS_HASH


def test_a_row_inserted_behind_the_applications_back_is_reported(db: Session) -> None:
    _write(db, 2)
    db.add(AuditLog(
        user_id=None, action=AuditAction.LOGIN, entity_type="users", entity_id=None,
        metadata_json=None, created_at=datetime.now(timezone.utc) + timedelta(seconds=5),
    ))
    db.commit()
    report = audit_chain.verify(db)
    assert report.ok is False  # review finding 17: an unchained row is not "ok"
    assert report.unchained_after_start == 1
    assert report.reason == audit_chain.REASON_UNCHAINED
    assert report.first_broken_seq is None and report.verified == 2


def test_a_rolled_back_audit_leaves_no_gap(db: Session) -> None:
    _write(db, 2)
    trail.audit(db, action=AuditAction.CREATE, entity_type="x", entity_id=None, actor=None)
    db.rollback()
    _write(db, 1)
    assert [r.seq for r in _rows(db)] == [1, 2, 3]
    assert audit_chain.verify(db).ok


def test_a_rolled_back_savepoint_does_not_fork_the_chain(db: Session) -> None:
    _write(db, 1)
    trail.audit(db, action=AuditAction.CREATE, entity_type="kept", entity_id=None, actor=None)
    nested = db.begin_nested()
    trail.audit(db, action=AuditAction.CREATE, entity_type="lost", entity_id=None, actor=None)
    nested.rollback()
    trail.audit(db, action=AuditAction.CREATE, entity_type="after", entity_id=None, actor=None)
    db.commit()
    assert [r.seq for r in _rows(db)] == [1, 2, 3]
    assert [r.entity_type for r in _rows(db)] == ["patients", "kept", "after"]
    assert audit_chain.verify(db).ok


# -- Endpoints ---------------------------------------------------------------


@pytest.fixture
def api(db: Session):  # noqa: ANN201
    def build(code: RoleCode) -> tuple[TestClient, User]:
        role = db.scalar(select(Role).where(Role.code == code)) or Role(
            code=code, name=code.value, description="Rol de prueba"
        )
        db.add(role)
        db.flush()
        account = User(
            username=f"{code.value.lower()}.prueba",
            full_name="Prueba",
            email=f"{code.value.lower()}@saludred.local",
            password_hash="no-se-usa",
            role_id=role.id,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: account
        return TestClient(app), account

    yield build
    app.dependency_overrides.clear()


def test_verify_is_admin_only(db: Session, api) -> None:
    client, _ = api(RoleCode.IPS_CLINICAL_OPERATOR)
    assert client.get("/api/v1/audit-logs/verify").status_code == 403
    assert client.get("/api/v1/audit-logs/head").status_code == 403


def test_verify_and_head_require_a_token() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/audit-logs/verify").status_code == 401
    assert client.get("/api/v1/audit-logs/head").status_code == 401


def test_the_admin_verifies_the_chain_and_the_verification_is_audited(db: Session, api) -> None:
    client, admin = api(RoleCode.ADMIN)
    _write(db, 4)

    response = client.get("/api/v1/audit-logs/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["filas_verificadas"] >= 4
    assert body["primera_fila_rota_seq"] is None
    assert len(body["ultimo_hash"]) == 64
    assert "borrar" in body["nota"]
    audited = db.scalars(
        select(AuditLog).where(AuditLog.action == AuditAction.AUDIT_CHAIN_VERIFIED)
    ).all()
    assert len(audited) == 1 and audited[0].user_id == admin.id
    assert audited[0].metadata_json["ok"] is True
    # The verification became the next link, and the chain still holds.
    assert client.get("/api/v1/audit-logs/verify").json()["ok"] is True


def test_the_admin_sees_the_first_broken_row(db: Session, api) -> None:
    client, _ = api(RoleCode.ADMIN)
    _write(db, 5)
    target = _rows(db)[3]
    db.execute(update(AuditLog).where(AuditLog.id == target.id).values(entity_type="otra-cosa"))
    db.commit()

    body = client.get("/api/v1/audit-logs/verify").json()

    assert body["ok"] is False
    assert body["primera_fila_rota_id"] == str(target.id)
    assert body["primera_fila_rota_seq"] == target.seq
    assert body["motivo"] == "contenido_alterado"


def test_head_returns_the_last_hash_to_publish(db: Session, api) -> None:
    client, _ = api(RoleCode.ADMIN)
    assert client.get("/api/v1/audit-logs/head").status_code == 404  # nothing yet
    _write(db, 3)
    body = client.get("/api/v1/audit-logs/head").json()
    last = _rows(db)[-1]
    assert body["seq"] == last.seq and body["ultimo_hash"] == last.row_hash


def test_the_audit_listing_still_works(db: Session, api) -> None:
    client, _ = api(RoleCode.ADMIN)
    _write(db, 2)
    response = client.get("/api/v1/audit-logs")
    assert response.status_code == 200 and response.json()["total"] == 2
