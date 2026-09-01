"""Database engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    # Neon closes idle connections; recycling before that avoids handing a dead
    # socket to a request, and pre-ping catches the ones that die anyway.
    pool_pre_ping=True,
    pool_recycle=280,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
