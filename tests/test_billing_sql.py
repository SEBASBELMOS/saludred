"""Epic 6 queries compiled for PostgreSQL without a server, plus the migration check.

SQLite accepts things PostgreSQL rejects, so the text compiled with the
PostgreSQL dialect is checked here, as ``test_interop_sql.py`` does.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from app.billing import service as billing_service
from app.models.enums import AuditAction
from app.models.identity import User
from app.services import notifications, recipients

ID = uuid.uuid4()


def _sql(statement) -> str:  # noqa: ANN001
    return str(statement.compile(dialect=postgresql.dialect()))


STATEMENTS = {
    "inbox": notifications.inbox_stmt(User(id=ID), read=False),
    "eligible_clinicians": recipients.eligible_clinicians_stmt((ID,)),
    "patient_encounter_orgs": recipients.patient_encounter_organizations_stmt(ID),
    "accountants_of_eps": recipients.accountants_of_eps_stmt(ID),
    "approved_reports_all": billing_service.approved_reports_stmt(None),
    "approved_reports_eps": billing_service.approved_reports_stmt(ID),
    "released_assignments_all": billing_service.released_assignments_stmt(None),
    "released_assignments_eps": billing_service.released_assignments_stmt(ID),
}


@pytest.mark.parametrize("name", STATEMENTS)
def test_no_is_not_with_a_bound_parameter(name) -> None:
    text = _sql(STATEMENTS[name])
    assert not re.search(r"IS NOT (?!NULL)", text), text


def test_inbox_uses_not_exists_so_notifications_without_patient_still_show() -> None:
    text = _sql(STATEMENTS["inbox"])
    assert "NOT (EXISTS" in text or "NOT EXISTS" in text
    assert "patients.deleted_at IS NOT NULL" in text
    assert "notifications.patient_id" in text
    assert "JOIN" not in text  # an inner join would drop rows with a NULL patient_id
    assert "notifications.is_read IS false" in text or "notifications.is_read = false" in text


def test_eligible_clinicians_filters_role_active_and_unlocked() -> None:
    text = _sql(STATEMENTS["eligible_clinicians"])
    assert "roles.code IN" in text and "users.is_active IS true" in text
    assert "users.locked_at IS NULL" in text and "users.organization_id IN" in text


def test_reconciliation_scans_are_scoped_and_filtered() -> None:
    reports = _sql(STATEMENTS["approved_reports_eps"])
    assert "ai_reports.status =" in reports and "patients.eps_organization_id =" in reports
    stays = _sql(STATEMENTS["released_assignments_eps"])
    assert "bed_assignments.released_at IS NOT NULL" in stays
    assert "bed_assignments.deleted_at IS NULL" in stays
    assert "patients.eps_organization_id =" in stays
    assert "eps_organization_id" not in _sql(STATEMENTS["released_assignments_all"])


def test_the_latest_migration_covers_every_audit_action() -> None:
    """The head migration widens the CHECK to the full AuditAction vocabulary."""

    path = next(Path("migrations/versions").glob("f6b3d8e1a742_*.py"))
    spec = importlib.util.spec_from_file_location("migration_f6b3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.AUDIT_ACTIONS) == {a.value for a in AuditAction}
    assert module.down_revision == "e5a9c2d4b681"
    assert len(module.AUDIT_ACTIONS) == len(set(module.AUDIT_ACTIONS))
