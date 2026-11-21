"""Process-wide agent: memory, harness and router graph, built once.

The application lifespan opens the memory (Postgres in production). Anything that
runs before that, or without a lifespan (tests), gets an in-memory agent lazily.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.services.agent import memory as agent_memory
from app.services.agent.flow import build_flow
from app.services.agent.harness import Harness, build_agent
from app.services.agent.model import build_model

logger = logging.getLogger(__name__)

_memory: agent_memory.Memory | None = None
_harness: Harness | None = None
_flow = None


def _build(memory: agent_memory.Memory, model=None, *, use_default_model: bool = True) -> Harness:  # noqa: ANN001
    chosen = build_model() if use_default_model else model
    agent = build_agent(chosen, memory.checkpointer, memory.store) if chosen is not None else None
    return Harness(agent, memory.store, timeout_s=get_settings().agent_timeout_seconds)


def get_harness() -> Harness:
    global _memory, _harness
    if _harness is None:
        _memory = _memory or agent_memory.in_memory()
        _harness = _build(_memory)
    return _harness


def get_flow():  # noqa: ANN201
    global _flow
    if _flow is None:
        _flow = build_flow(get_harness)
    return _flow


def set_harness(harness: Harness | None) -> None:
    """Install a ready harness (tests) or reset to lazy defaults with ``None``."""

    global _harness, _memory
    _harness = harness
    if harness is None:
        _memory = None


async def startup() -> None:
    """Open the durable memory and build the harness. Never raises."""

    global _memory, _harness
    memory: agent_memory.Memory | None = None
    try:
        memory = await agent_memory.open_memory()
        harness = _build(memory)
        _memory, _harness = memory, harness
        logger.info(
            "Clinical agent ready: memory=%s model=%s",
            memory.backend,
            "groq" if harness.agent is not None else "none (extractive fallback)",
        )
    except Exception:  # noqa: BLE001 -- the API must start without the agent
        logger.exception("Clinical agent startup failed; it will start in-memory on first use")
        _memory = _harness = None
        if memory is not None:  # the pool was opened before the build failed: do not leak it
            try:
                await memory.close()
            except Exception:  # noqa: BLE001
                logger.exception("Clinical agent memory pool could not be closed")


async def shutdown() -> None:
    global _memory, _harness
    if _memory is not None:
        await _memory.close()
    _memory = _harness = None
