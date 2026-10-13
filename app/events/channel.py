"""Live channel (R17): one asyncio queue per open SSE connection.

The registry maps a user id to its open connections (a person can have several
tabs). It lives in process memory, so it only works with a SINGLE API worker or
replica: an event published in worker A never reaches a connection held by
worker B. ``docs/EVENTOS.md`` explains the constraint and the way out (Redis
pub/sub) if the API is ever scaled out.

Threads: ``publish`` normally runs inside a sync route handler, which FastAPI
executes in a thread pool, while the queues belong to the server's event loop
and are not thread-safe. Pushing therefore goes through
``loop.call_soon_threadsafe`` with the loop captured when the stream started.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import RoleCode

logger = logging.getLogger(__name__)

QUEUE_SIZE = 100

# Clinical patient events are never delivered to these roles (R17).
NON_CLINICAL_ROLES = (RoleCode.PATIENT, RoleCode.ACCOUNTANT)


@dataclass(eq=False)
class Connection:
    user_id: uuid.UUID
    role: RoleCode
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE)
    )
    # ``None`` = every event. Otherwise only these ``tipo`` values are delivered
    # (``?tipos=a,b`` on the stream). Heartbeats never go through the registry,
    # so they are not affected.
    tipos: frozenset[str] | None = None

    def wants(self, payload: dict[str, Any]) -> bool:
        return self.tipos is None or payload.get("tipo") in self.tipos


def parse_tipos(raw: str | None) -> frozenset[str] | None:
    """``"a, b"`` -> ``{"a", "b"}``; empty or missing means no filter."""

    if not raw:
        return None
    wanted = frozenset(item.strip() for item in raw.split(",") if item.strip())
    return wanted or None


def _offer(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
    """Runs on the event loop. A slow consumer loses its oldest event, not the stream."""

    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover -- raced with the consumer
            pass
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:  # pragma: no cover
        logger.warning("Dropping a live event: the connection queue is full")


class ChannelRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[uuid.UUID, set[Connection]] = {}

    def subscribe(
        self,
        user_id: uuid.UUID,
        role: RoleCode,
        loop: asyncio.AbstractEventLoop,
        tipos: frozenset[str] | None = None,
    ) -> Connection:
        connection = Connection(user_id=user_id, role=role, loop=loop, tipos=tipos)
        with self._lock:
            self._connections.setdefault(user_id, set()).add(connection)
        return connection

    def unsubscribe(self, connection: Connection) -> None:
        with self._lock:
            bucket = self._connections.get(connection.user_id)
            if bucket is None:
                return
            bucket.discard(connection)
            if not bucket:
                del self._connections[connection.user_id]

    def connection_count(self, user_id: uuid.UUID | None = None) -> int:
        with self._lock:
            if user_id is not None:
                return len(self._connections.get(user_id, ()))
            return sum(len(bucket) for bucket in self._connections.values())

    def push(self, user_id: uuid.UUID, payload: dict[str, Any], *, clinical: bool = True) -> int:
        """Deliver to every open connection of ``user_id``; return how many."""

        with self._lock:
            targets = list(self._connections.get(user_id, ()))

        delivered = 0
        for connection in targets:
            # Second line of defence after the reactor: even if a patient or
            # accountant were listed as recipient, a clinical event does not
            # reach their connection.
            if clinical and connection.role in NON_CLINICAL_ROLES:
                continue
            if not connection.wants(payload):
                continue
            try:
                connection.loop.call_soon_threadsafe(_offer, connection.queue, payload)
                delivered += 1
            except RuntimeError:
                # The loop is closed: the connection is dead, forget it.
                logger.info("Removing a connection whose event loop is closed")
                self.unsubscribe(connection)
        return delivered

    def clear(self) -> None:
        with self._lock:
            self._connections.clear()


registry = ChannelRegistry()
