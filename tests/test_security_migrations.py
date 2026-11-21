"""The two security migrations are additive, chained after the agent head and reversible."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load(revision: str):  # noqa: ANN202
    path = next(Path("migrations/versions").glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.source = path.read_text()
    return module


def test_the_migrations_chain_after_the_agent_head() -> None:
    assert _load("e8b3a5c7d921").down_revision == "a2f7c4d19b60"
    assert _load("f1d6c4a8b250").down_revision == "e8b3a5c7d921"


def test_the_encryption_migration_adds_the_index_widens_columns_and_rewrites_no_row() -> None:
    module = _load("e8b3a5c7d921")
    source = module.source
    assert '"documento_idx"' in source and module.UNIQUE_INDEX in source
    assert {column for column, _, _ in module.WIDENED} == {
        "document_number", "phone", "email", "address",
    }
    # Additive: nothing rewrites or deletes data, and the old constraint stays.
    for forbidden in ("UPDATE patients", "DELETE FROM", "DROP CONSTRAINT", "drop_constraint"):
        assert forbidden not in source
    # The view is dropped BEFORE the columns are retyped (PostgreSQL refuses otherwise)
    # and created again afterwards, in both directions.
    up = source.split("def upgrade()")[1].split("def downgrade()")[0]
    assert up.index("DROP VIEW") < up.index("alter_column") < up.index("op.execute(VIEW_SQL)")
    down = source.split("def downgrade()")[1]
    assert down.index("DROP VIEW") < down.index("alter_column") < down.index("VIEW_SQL_PREVIOUS")


def test_the_view_no_longer_exposes_the_document() -> None:
    module = _load("e8b3a5c7d921")
    assert "paciente_documento" not in module.VIEW_SQL
    assert "paciente_documento" in module.VIEW_SQL_PREVIOUS
    # Everything else of the view is unchanged.
    assert module.VIEW_SQL.count("JOIN") == module.VIEW_SQL_PREVIOUS.count("JOIN")
    assert module.VIEW_SQL.count(" AS ") == module.VIEW_SQL_PREVIOUS.count(" AS ") - 1


def test_the_audit_chain_migration_is_additive_and_reversible() -> None:
    module = _load("f1d6c4a8b250")
    source = module.source
    for column in ('"seq"', '"prev_hash"', '"row_hash"'):
        assert f"add_column(\"audit_log\", sa.Column({column}" in source
    up = source.split("def upgrade()")[1].split("def downgrade()")[0]
    down = source.split("def downgrade()")[1]
    # Existing rows are not numbered or hashed (no identity column, no UPDATE).
    for forbidden in ("Identity", "UPDATE", "server_default", "nullable=False"):
        assert forbidden not in up
    for dropped in ("row_hash", "prev_hash", "seq"):
        assert f'drop_column("audit_log", "{dropped}")' in down
    assert "drop_index" in down


@pytest.mark.parametrize("revision", ["e8b3a5c7d921", "f1d6c4a8b250"])
def test_revisions_have_the_expected_id(revision: str) -> None:
    assert _load(revision).revision == revision
