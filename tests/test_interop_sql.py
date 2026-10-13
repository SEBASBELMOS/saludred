"""The new queries, compiled for PostgreSQL without a server.

SQLite accepts things PostgreSQL rejects (last epic: ``IS NOT`` with a bound
parameter). Compiling with the PostgreSQL dialect and checking the text catches
that class of bug before it reaches Neon.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.models.identity import User
from app.services import fhir_inbound, fhir_sync, notifications, recipients

ID = uuid.uuid4()


def _sql(statement) -> str:  # noqa: ANN001
    return str(statement.compile(dialect=postgresql.dialect()))


STATEMENTS = {
    "patient_by_fhir_id": fhir_inbound.patient_by_fhir_id_stmt("pat-1"),
    "inbound_event": fhir_inbound.inbound_event_stmt("Observation", "1", "1"),
    "target_encounter_open": fhir_inbound.target_encounter_stmt(ID, open_only=True),
    "target_encounter_latest": fhir_inbound.target_encounter_stmt(ID, open_only=False),
    "latest_encounter_org": recipients.latest_encounter_organization_stmt(ID),
    "clinicians_of_org": recipients.clinicians_of_organization_stmt(ID),
    "admins": recipients.admins_stmt(),
    "unsynced_patients": fhir_sync.unsynced_patients_stmt(50),
}


@pytest.mark.parametrize("name", STATEMENTS)
def test_no_is_not_with_a_bound_parameter(name) -> None:
    text = _sql(STATEMENTS[name])
    assert not re.search(r"IS NOT (?!NULL)", text), text


def test_unsynced_patients_is_a_left_join_with_the_right_filter() -> None:
    text = _sql(STATEMENTS["unsynced_patients"])
    assert "LEFT OUTER JOIN fhir_sync_log" in text
    assert "fhir_sync_log.id IS NULL" in text
    assert "fhir_sync_log.sync_status IN" in text
    assert "patients.deleted_at IS NULL" in text and "LIMIT" in text


def test_latest_encounter_orders_by_start_desc_and_limits_one() -> None:
    text = _sql(STATEMENTS["latest_encounter_org"])
    assert "ORDER BY encounters.started_at DESC" in text and "LIMIT" in text


def test_target_encounter_open_filter() -> None:
    text = _sql(STATEMENTS["target_encounter_open"])
    assert "encounters.ended_at IS NULL" in text and "encounters.status IN" in text


def test_clinicians_filter_active_unlocked_accounts_of_the_ips() -> None:
    text = _sql(STATEMENTS["clinicians_of_org"])
    assert "users.is_active IS true" in text and "users.locked_at IS NULL" in text
    assert "users.organization_id =" in text


class _Probe:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def scalars(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.sql.append(_sql(statement))
        return []


@pytest.mark.parametrize("read", [None, True, False])
def test_inbox_queries(read) -> None:
    probe = _Probe()
    notifications.list_for_user(probe, User(id=ID), read=read)
    assert probe.sql
    for text in probe.sql:
        assert not re.search(r"IS NOT (?!NULL)", text), text
        assert "notifications.user_id =" in text
        assert "ORDER BY notifications.created_at DESC" in text
