"""Database sessions for the agent's tools and middleware.

The agent runs outside the request's dependency injection (tools execute in
worker threads, possibly after the request that started the run), so each tool
call opens its own short session. Tests install a factory over the shared
in-memory database (``set_session_factory``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy.orm import Session

_factory: Callable[[], AbstractContextManager[Session]] | None = None


def set_session_factory(factory: Callable[[], AbstractContextManager[Session]] | None) -> None:
    global _factory
    _factory = factory


@contextmanager
def session_scope() -> Iterator[Session]:
    if _factory is not None:
        with _factory() as session:
            yield session
        return
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
