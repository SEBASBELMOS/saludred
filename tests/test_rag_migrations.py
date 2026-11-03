"""The RAG migrations stay in step with the model and the audit enum."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models.enums import AuditAction, RagAnswerMode, RagBase, RagSuggestionStatus

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"


def _load(prefix: str):  # noqa: ANN202
    path = next(VERSIONS.glob(f"{prefix}_*.py"))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_check_lists_every_action_of_the_enum() -> None:
    module = _load("c9e4a6b2d503")
    assert set(module.AUDIT_ACTIONS) == {a.value for a in AuditAction}
    assert len(module.AUDIT_ACTIONS) == len(set(module.AUDIT_ACTIONS))
    assert set(module.AUDIT_ACTIONS) - set(module.PREVIOUS_AUDIT_ACTIONS) == {
        "RAG_QUERY", "RAG_SUGGESTION_APPROVED", "RAG_SUGGESTION_REJECTED",
    }


def test_enum_checks_match_the_models() -> None:
    chunks, suggestions = _load("b8d3f5a1c492"), _load("c9e4a6b2d503")
    assert set(chunks.BASES) == {b.value for b in RagBase}
    assert set(suggestions.MODES) == {m.value for m in RagAnswerMode}
    assert set(suggestions.STATUSES) == {s.value for s in RagSuggestionStatus}


def test_migrations_chain_from_the_previous_head() -> None:
    assert _load("b8d3f5a1c492").down_revision == "a7c4e9d2b815"
    assert _load("c9e4a6b2d503").down_revision == "b8d3f5a1c492"
