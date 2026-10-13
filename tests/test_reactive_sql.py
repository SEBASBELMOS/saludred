"""The Epic 7 queries compiled for PostgreSQL without a server, plus the migration.

SQLite accepts things PostgreSQL rejects (an earlier epic hit ``IS NOT`` with a
bound parameter; ``ORDER BY`` inside a ``UNION`` is another). Compiling with the
PostgreSQL dialect and checking the text catches that class of bug before it
reaches Neon. Same pattern as ``test_interop_sql.py`` and ``test_billing_sql.py``.
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.billing import service as billing_service
from app.models import Alert, Referral
from app.models.enums import ReferralStatus, RoleCode
from app.services import ai_reports, alerts, patient_access, pending, recipients, referrals
from tests.factories import make_user

ID = uuid.uuid4()
ORG = uuid.uuid4()


def _sql(statement) -> str:  # noqa: ANN001
    return str(statement.compile(dialect=postgresql.dialect()))


def _where(statement) -> str:  # noqa: ANN001
    """Only the WHERE part: the SELECT list names every column of the table."""

    return _sql(statement).split("WHERE", 1)[1]


def _user(role: RoleCode):  # noqa: ANN202
    user = make_user(role, organization_id=ORG)
    user.id = ID
    return user


MEDICO = _user(RoleCode.IPS_CLINICAL_OPERATOR)
SPECIALIST = _user(RoleCode.SPECIALIST)
ADMIN = _user(RoleCode.ADMIN)
COORDINATOR = _user(RoleCode.EPS_COORDINATOR)

STATEMENTS = {
    "alerts_medico": alerts.alerts_stmt(MEDICO),
    "alerts_specialist": alerts.alerts_stmt(SPECIALIST),
    "alerts_admin": alerts.alerts_stmt(ADMIN),
    "alerts_coordinator": alerts.alerts_stmt(COORDINATOR),
    "alerts_active_only": alerts.alerts_stmt(MEDICO, estado=alerts.AlertStatus.ACTIVE),
    "count_alerts_medico": alerts.active_count_stmt(MEDICO),
    "count_alerts_specialist": alerts.active_count_stmt(SPECIALIST),
    "count_alerts_admin": alerts.active_count_stmt(ADMIN),
    "overdue_unacknowledged": alerts.overdue_unacknowledged_stmt(datetime.now(timezone.utc)),
    "active_bed_alerts": alerts.active_bed_alerts_stmt(),
    "pending_reports_medico": ai_reports.pending_count_stmt(MEDICO),
    "pending_reports_specialist": ai_reports.pending_count_stmt(SPECIALIST),
    "reports_specialist": ai_reports.reports_stmt(SPECIALIST),
    "referrals_received": pending.referrals_received_stmt(SPECIALIST),
    "referrals_specialist": referrals.referrals_stmt(SPECIALIST),
    "referrals_medico": referrals.referrals_stmt(MEDICO, estado=ReferralStatus.PENDING),
    "referrals_admin": referrals.referrals_stmt(ADMIN, rol="recibidas"),
    "specialists": referrals.specialists_stmt(),
    "referred_patients": patient_access.referred_patient_ids(SPECIALIST),
    "patient_audience": recipients.patient_audience_stmt(ID),
    "coordinators": recipients.coordinators_stmt(),
    "operators_of_org": recipients.operators_of_organization_stmt(ID),
    "billable_referrals": billing_service.billable_referrals_stmt(ID),
    "billable_referrals_all": billing_service.billable_referrals_stmt(None),
}


@pytest.mark.parametrize("name", STATEMENTS)
def test_every_statement_compiles_for_postgresql(name) -> None:
    text = _sql(STATEMENTS[name])
    # The class of bug SQLite hides: IS NOT against something that is not NULL.
    assert not re.search(r"IS NOT (?!NULL)", text), text
    assert "SELECT" in text


def test_a_union_has_no_order_by_inside_its_branches() -> None:
    """PostgreSQL rejects ``ORDER BY`` in a ``UNION`` branch without parentheses."""

    text = _sql(STATEMENTS["patient_audience"])
    assert "UNION" in text and "ORDER BY" not in text
    assert "encounters.deleted_at IS NULL" in text
    assert "referrals.estado IN" in text and "users.locked_at IS NULL" in text


def test_the_visibility_clause_per_role() -> None:
    medico = _sql(STATEMENTS["alerts_medico"])
    assert "alerts.recipient_user_id =" in medico and "alerts.organization_id =" in medico
    assert "encounters.deleted_at IS NULL" in medico
    assert "patients.deleted_at IS NOT NULL" in medico  # NOT EXISTS (deleted patient)
    specialist = _sql(STATEMENTS["alerts_specialist"])
    assert "referrals.specialist_user_id =" in specialist and "referrals.estado IN" in specialist
    assert "cama_sin_limpiar" not in specialist  # a specialist is not an addressee of bed alerts
    coordinator = _where(STATEMENTS["alerts_coordinator"])
    assert "alerts.kind =" in coordinator and "recipient_user_id" not in coordinator
    admin = _where(STATEMENTS["alerts_admin"])
    assert "alerts.recipient_user_id" not in admin and "alerts.kind =" not in admin


def test_the_counters_are_plain_counts_that_skip_deleted_patients() -> None:
    for name in ("count_alerts_medico", "count_alerts_admin", "pending_reports_medico", "referrals_received"):
        text = _sql(STATEMENTS[name])
        assert "count(*)" in text.lower() and "deleted_at" in text, name
    reports = _sql(STATEMENTS["pending_reports_specialist"])
    assert "ai_reports.status =" in reports and "patients.deleted_at IS NULL" in reports
    assert "referrals.estado IN" in reports  # the referral grant widens the specialist's scope
    received = _sql(STATEMENTS["referrals_received"])
    assert "referrals.specialist_user_id =" in received and "referrals.estado =" in received


def test_the_escalation_query_selects_only_unescalated_active_alerts() -> None:
    text = _sql(STATEMENTS["overdue_unacknowledged"])
    assert "alerts.estado =" in text and "alerts.escalated_at IS NULL" in text
    assert "alerts.created_at <=" in text and "ORDER BY alerts.created_at" in text


def test_the_referral_list_filters_by_side_and_state() -> None:
    received = _sql(STATEMENTS["referrals_specialist"])
    assert "referrals.specialist_user_id =" in received
    assert "referring_user_id =" not in received.split("WHERE", 1)[1]
    sent = _sql(STATEMENTS["referrals_medico"])
    assert "referrals.referring_user_id =" in sent and "referrals.estado =" in sent
    assert "patients.deleted_at IS NULL" in sent
    admin = _sql(STATEMENTS["referrals_admin"])
    assert "referrals.specialist_user_id =" not in admin  # the admin sees all, whatever ``rol``


def test_the_picker_lists_active_unlocked_specialists_with_their_ips() -> None:
    text = _sql(STATEMENTS["specialists"])
    assert "LEFT OUTER JOIN organizations" in text
    assert "users.is_active IS true" in text and "users.locked_at IS NULL" in text


def test_the_referral_grant_is_a_subquery_of_live_states() -> None:
    text = _sql(STATEMENTS["referred_patients"])
    assert text.startswith("SELECT referrals.patient_id")
    assert "referrals.specialist_user_id =" in text and "referrals.estado IN" in text
    assert patient_access.referred_patient_ids(MEDICO) is None  # only specialists hold one


def test_the_billing_reconciliation_covers_accepted_and_completed_referrals() -> None:
    text = _sql(STATEMENTS["billable_referrals"])
    assert "referrals.estado IN" in text and "patients.eps_organization_id =" in text
    assert "eps_organization_id" not in _sql(STATEMENTS["billable_referrals_all"])


def test_the_new_tables_and_partial_unique_indexes_in_postgresql_ddl() -> None:
    referrals_ddl = str(CreateTable(Referral.__table__).compile(dialect=postgresql.dialect()))
    assert "CONSTRAINT ck_referrals_referral_status CHECK (estado IN ('pendiente', 'aceptada', 'completada', 'rechazada'))" in referrals_ddl
    assert "motivo VARCHAR(1000) NOT NULL" in referrals_ddl
    assert "informe_especialista TEXT" in referrals_ddl and "fhir_resource_id" in referrals_ddl
    alerts_ddl = str(CreateTable(Alert.__table__).compile(dialect=postgresql.dialect()))
    assert "ck_alerts_alert_kind" in alerts_ddl and "paciente_critico" in alerts_ddl
    assert "ck_alerts_alert_status CHECK (estado IN ('activa', 'reconocida'))" in alerts_ddl
    assert "escalated_to JSONB" in alerts_ddl

    indexes = {i.name: str(CreateIndex(i).compile(dialect=postgresql.dialect())) for i in Alert.__table__.indexes}
    critical = indexes["uq_alerts_active_critical_patient"]
    assert "CREATE UNIQUE INDEX uq_alerts_active_critical_patient ON alerts (patient_id)" in critical
    assert "WHERE kind = 'paciente_critico' AND estado = 'activa'" in critical
    bed = indexes["uq_alerts_active_bed"]
    assert "WHERE kind = 'cama_sin_limpiar' AND estado = 'activa'" in bed


def test_the_migration_creates_both_tables_and_extends_the_audit_check() -> None:
    path = next(Path("migrations/versions").glob("f6b3d8e1a742_*.py"))
    spec = importlib.util.spec_from_file_location("migration_epic7", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "f6b3d8e1a742" and module.down_revision == "e5a9c2d4b681"
    assert {"REFERRAL_REJECTED", "REFERRAL_COMPLETED"} <= set(module.AUDIT_ACTIONS)
    assert set(module.AUDIT_ACTIONS) - set(module.PREVIOUS_AUDIT_ACTIONS) == {
        "REFERRAL_REJECTED", "REFERRAL_COMPLETED",
    }
    source = path.read_text()
    for needle in ('"referrals"', '"alerts"', "uq_alerts_active_critical_patient", "uq_alerts_active_bed"):
        assert needle in source


def test_there_is_a_single_migration_head() -> None:
    """Two heads would make ``alembic upgrade head`` ambiguous."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    assert ScriptDirectory.from_config(config).get_heads() == ["f6b3d8e1a742"]
