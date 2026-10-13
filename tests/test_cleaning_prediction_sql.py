"""The cleaning-prediction queries, compiled for PostgreSQL without a server.

SQLite accepts ``status IS NOT 'X'``; PostgreSQL rejects it when the right side
is a bind parameter (``IS NOT $3``). Compiling with the PostgreSQL dialect and
checking the text catches that before it reaches a real database.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.dialects import postgresql

from app.models.enums import BedStatus, LocationType
from app.models.organization import Location
from app.services import cleaning_prediction


class _ProbeSession:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def scalar(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return 0


def test_features_query_has_no_is_not_with_a_parameter() -> None:
    bed = Location(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        code="B-1",
        name="Bed 1",
        location_type=LocationType.BED,
        status=BedStatus.CLEANING,
        service="Urgencias",
    )
    probe = _ProbeSession()
    cleaning_prediction.build_features(probe, bed)
    assert probe.sql
    for statement in probe.sql:
        assert not re.search(r"IS NOT (?!NULL)", statement), statement
