"""Checkpointer (short-term, per thread) and store (long-term) of the agent.

Production: Postgres, in the application database (``langgraph-checkpoint-postgres``,
psycopg 3 through a pool, so a connection that Neon closed is replaced instead of
reused). The library creates its own tables (``checkpoints*``, ``store*``) on
``setup()``; they are not Alembic-managed. If Postgres cannot start the agent
falls back to memory and logs it: it still works, but loses threads on restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Memory:
    checkpointer: Any
    store: Any
    backend: str  # "postgres" | "memory"
    pool: Any = None

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()


def in_memory() -> Memory:
    return Memory(InMemorySaver(), InMemoryStore(), "memory")


def _conninfo(database_url: str) -> str:
    # SQLAlchemy writes postgresql+psycopg://; psycopg wants a plain URL.
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def open_memory() -> Memory:
    settings = get_settings()
    if settings.agent_memory != "postgres" or not settings.database_url.startswith("postgresql"):
        return in_memory()
    pool = None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            _conninfo(settings.database_url),
            min_size=1,
            max_size=4,
            open=False,
            # autocommit + dict rows are what the savers expect; prepare_threshold=0
            # keeps it working behind a transaction pooler such as Neon's.
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            check=AsyncConnectionPool.check_connection,
            max_idle=240,
        )
        await pool.open(wait=True, timeout=15)
        checkpointer = AsyncPostgresSaver(pool)
        store = AsyncPostgresStore(pool)
        await checkpointer.setup()
        await store.setup()
        return Memory(checkpointer, store, "postgres", pool)
    except Exception:  # noqa: BLE001 -- the agent must start even if its memory cannot
        logger.exception("Agent memory: Postgres unavailable, falling back to in-memory")
        if pool is not None:
            try:
                await pool.close()
            except Exception:  # noqa: BLE001
                pass
        return in_memory()
