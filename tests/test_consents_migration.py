"""WU1 — the consent revision: chained after the interop head, additive, reversible.

Static parity tests follow the tests/test_rag_migrations.py pattern. The
executable tests run the revision's upgrade()/downgrade() against an in-memory
SQLite holding a minimal pre-e2 ``audit_log`` — the same disposable-database idea
used for the manual alembic cycle on the ASUS machine. Nothing here touches a
real database.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.models.enums import AuditAction

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
INTEROP_HEAD = "b7e5d9a3c418"
AUDIT_HEAD = "a2f7c4d19b60"


def _load(path: Path):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _consents_module():  # noqa: ANN202
    modules = [_load(p) for p in sorted(VERSIONS.glob("*_*.py"))]
    matches = [m for m in modules if getattr(m, "down_revision", None) == INTEROP_HEAD]
    assert len(matches) == 1, (
        f"expected exactly one revision whose down_revision is {INTEROP_HEAD}, "
        f"found {len(matches)}"
    )
    return matches[0]


def _audit_head_module():  # noqa: ANN202
    return _load(next(VERSIONS.glob(f"{AUDIT_HEAD}_*.py")))


def _audit_log_ddl() -> str:
    actions = ", ".join(f"'{a}'" for a in _audit_head_module().AUDIT_ACTIONS)
    return (
        "CREATE TABLE audit_log ("
        "id VARCHAR(36) PRIMARY KEY, "
        "action VARCHAR(32) NOT NULL, "
        # The name the naming convention produces (ck_<table>_<constraint>):
        # alembic renames schema objects through the target metadata, so this is
        # the name the production table carries.
        f"CONSTRAINT ck_audit_log_audit_action CHECK (action IN ({actions}))"
        ")"
    )


@pytest.fixture
def migration_engine():
    """In-memory SQLite with the pre-e2 audit_log, the state after b7e5d9a3c418."""

    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text(_audit_log_ddl()))
    return engine


def _run(engine, function) -> None:  # noqa: ANN001
    from app.models import Base

    with engine.begin() as conn:
        # target_metadata carries the naming convention exactly as env.py passes
        # it, so the test exercises the same renaming the real run does.
        context = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(context):
            function()


# ---------------------------------------------------------------------------
# Static parity
# ---------------------------------------------------------------------------


def test_the_consents_revision_follows_the_interop_head() -> None:
    module = _consents_module()
    assert module.down_revision == INTEROP_HEAD
    assert module.revision != INTEROP_HEAD


def test_audit_actions_cover_the_enum() -> None:
    module = _consents_module()
    assert set(module.AUDIT_ACTIONS) == {a.value for a in AuditAction}
    assert len(module.AUDIT_ACTIONS) == len(set(module.AUDIT_ACTIONS))
    assert set(module.AUDIT_ACTIONS) - set(module.PREVIOUS_AUDIT_ACTIONS) == {
        "CONSENT_CREATED",
        "CONSENT_REVOKED",
    }
    assert set(module.PREVIOUS_AUDIT_ACTIONS) == set(_audit_head_module().AUDIT_ACTIONS)


def test_the_exactly_one_destination_check_is_sqlite_safe() -> None:
    """The CHECK must be plain IS NULL/IS NOT NULL logic: SQLite cannot enforce
    row-counting or set-returning CHECK expressions, and the ASUS verification
    cycle runs the revision against SQLite."""

    module = _consents_module()
    for half in (
        "organization_id IS NULL",
        "interop_client_id IS NOT NULL",
        "organization_id IS NOT NULL",
        "interop_client_id IS NULL",
    ):
        assert half in module.EXACTLY_ONE_DESTINATION


# ---------------------------------------------------------------------------
# Executable: the real DDL on SQLite
# ---------------------------------------------------------------------------


def test_upgrade_creates_the_table_with_check_and_indexes(migration_engine) -> None:
    module = _consents_module()
    _run(migration_engine, module.upgrade)

    inspector = inspect(migration_engine)
    assert "consents" in inspector.get_table_names()
    index_names = {i["name"] for i in inspector.get_indexes("consents")}
    assert {
        "ix_consents_patient_id_organization_id",
        "ix_consents_patient_id_interop_client_id",
    } <= index_names

    with migration_engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'consents'")
        ).scalar()
    assert "CONSTRAINT ck_consents_exactly_one_destination CHECK" in ddl
    assert "patient_id" in ddl and "valid_from" in ddl


def test_the_check_rejects_two_and_zero_destinations(migration_engine) -> None:
    from tests.factories import make_consent

    module = _consents_module()
    _run(migration_engine, module.upgrade)

    def _insert(consent) -> None:  # noqa: ANN001
        with migration_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO consents (id, patient_id, organization_id, "
                    "interop_client_id, valid_from) VALUES "
                    "(:id, :patient_id, :organization_id, :interop_client_id, :valid_from)"
                ),
                {
                    "id": str(consent.id),
                    "patient_id": str(consent.patient_id),
                    "organization_id": (
                        str(consent.organization_id) if consent.organization_id else None
                    ),
                    "interop_client_id": (
                        str(consent.interop_client_id) if consent.interop_client_id else None
                    ),
                    "valid_from": consent.valid_from,
                },
            )

    with pytest.raises(IntegrityError):
        _insert(make_consent(organization_id=uuid.uuid4(), interop_client_id=uuid.uuid4()))
    with pytest.raises(IntegrityError):
        _insert(make_consent(organization_id=None, interop_client_id=None))
    # Exactly one destination: accepted.
    _insert(make_consent())
    _insert(make_consent(interop_client_id=None, organization_id=uuid.uuid4()))


def test_downgrade_drops_the_table_and_restores_the_audit_check(migration_engine) -> None:
    module = _consents_module()
    _run(migration_engine, module.upgrade)
    _run(migration_engine, module.downgrade)

    assert "consents" not in inspect(migration_engine).get_table_names()
    with migration_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text("INSERT INTO audit_log (id, action) VALUES ('x1', 'CONSENT_CREATED')")
            )
        conn.execute(text("INSERT INTO audit_log (id, action) VALUES ('x2', 'LOGIN')"))


def test_create_all_builds_the_same_table_as_the_migration() -> None:
    """The model registered in app.models must match the revision: same table,
    same CHECK name (the naming convention renders ck_consents_<name>), same
    indexes, and no soft-delete columns."""

    from app.models import Base

    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "consents" in inspector.get_table_names()
    with engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'consents'")
        ).scalar()
    assert "CONSTRAINT ck_consents_exactly_one_destination CHECK" in ddl
    assert {
        "ix_consents_patient_id_organization_id",
        "ix_consents_patient_id_interop_client_id",
    } <= {i["name"] for i in inspector.get_indexes("consents")}
    columns = {c["name"] for c in inspector.get_columns("consents")}
    assert "deleted_at" not in columns
