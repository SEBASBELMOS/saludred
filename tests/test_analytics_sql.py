"""The analytics queries, compiled for PostgreSQL without a server.

Most analytics use PostgreSQL-only SQL (``percentile_cont``, ``age``,
``date_trunc``), so the in-memory SQLite schema cannot run them. Compiling the
statements with the PostgreSQL dialect still catches the class of error that
slipped through once: SQL that is valid for SQLAlchemy but that PostgreSQL
rejects at execution time.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Float, Numeric
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import Cast
from sqlalchemy.sql.functions import FunctionElement

from app.services import analytics


class _Compiled(Exception):
    """Raised by the probe session to stop right after compiling."""


class _ProbeSession:
    """Compiles the first statement it receives instead of running it."""

    def __init__(self) -> None:
        self.statements: list = []

    def execute(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        statement.compile(dialect=postgresql.dialect())
        self.statements.append(statement)
        raise _Compiled

    def scalar(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return self.execute(statement)


QUERIES = [
    analytics.wait_time_statistics,
    analytics.bed_turnaround_statistics,
    analytics.patient_cohorts,
    analytics.occupancy_timeline,
    analytics.decision_summary,
]


def _statement(query):  # noqa: ANN001, ANN202
    probe = _ProbeSession()
    with pytest.raises(_Compiled):
        query(probe)
    return probe.statements[0]


@pytest.mark.parametrize("query", QUERIES, ids=lambda q: q.__name__)
def test_each_query_compiles_for_postgresql(query) -> None:  # noqa: ANN001
    sql = str(_statement(query).compile(dialect=postgresql.dialect()))
    assert "SELECT" in sql


@pytest.mark.parametrize("query", QUERIES, ids=lambda q: q.__name__)
def test_rounding_always_happens_on_numeric(query) -> None:  # noqa: ANN001
    """Regression: ``round(double precision, integer)`` does not exist.

    PostgreSQL only defines two-argument ``round`` for ``numeric``. Rounding a
    ``double precision`` -- what ``extract(epoch ...)`` and
    ``percentile_cont`` return -- fails at execution with UndefinedFunction,
    and the wait-time panel answered 500 on the first real deployment.
    """

    # Se recorre el arbol de la consulta, no el texto: en el SQL compilado los
    # CAST anidados hacen que cualquier busqueda por patron confunda el tipo
    # del argumento interno con el del externo.
    rounds = [
        element
        for element in visitors.iterate(_statement(query))
        if isinstance(element, FunctionElement) and element.name == "round"
    ]
    if query is not analytics.occupancy_timeline and query is not analytics.decision_summary:
        # Sin esto, un recorrido que no encontrara nada pasaria igual.
        assert rounds, "no se encontro ningun round que revisar"
    for call in rounds:
        argument = list(call.clauses)[0]
        tipo = getattr(argument, "type", None)
        # Float es subclase de Numeric en SQLAlchemy: isinstance solo no basta.
        assert isinstance(argument, Cast) and isinstance(tipo, Numeric) and not isinstance(tipo, Float), (
            f"round sobre {type(tipo).__name__}"
        )
