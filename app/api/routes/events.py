"""Live channel (R17) over Server-Sent Events and over WebSocket.

``GET /api/v1/events/stream?token=<jwt>`` (SSE) and
``GET /api/v1/events/ws?token=<jwt>`` (WebSocket) feed from the SAME in-process
registry and send the same JSON. SSE is the channel for the local network;
WebSocket is the one that crosses Cloudflare quick tunnels, which do not
deliver an SSE body (see ``docs/EVENTOS.md``).

The browser's ``EventSource`` and ``WebSocket`` cannot send headers, so the
token travels in the query string; it is the same JWT the rest of the API
uses, decoded and checked against the database in the same way (and a locked
account is refused as well).

A user receives only events addressed to them. The stream is best effort: the
inbox (``/api/v1/notifications``) is the source of truth, and a client that
reconnects should re-read it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.deps import DbSession, user_from_token
from app.core.database import get_db
from app.events.channel import Connection, parse_tipos, registry
from app.models.enums import RoleCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["tiempo real"])

HEARTBEAT_SECONDS = 15.0
RECONNECT_MS = 3000
# Application close code for a refused credential. The browser sees it in
# CloseEvent.code and must NOT keep reconnecting with the same token.
WS_UNAUTHORIZED = 4401
TIPOS_HELP = (
    "Lista separada por comas de tipos de evento a recibir (por ejemplo "
    "'alerta_critica,remision_creada'). Sin ella se reciben todos."
)


class StreamIdentity:
    """What the stream needs from the user, copied out of the ORM session.

    The database session is released right after authentication: a stream can
    stay open for hours, and a pooled connection must not stay pinned to it.
    """

    def __init__(self, user_id, role: RoleCode) -> None:  # noqa: ANN001
        self.user_id = user_id
        self.role = role


def authenticate_token(db: Session, token: str | None) -> StreamIdentity:
    """Shared by SSE and WebSocket: raise 401 unless the token names a usable account."""

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un token de acceso (?token=)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # ``user_from_token`` refuses disabled and locked accounts, the same check
    # every authenticated request goes through.
    user = user_from_token(db, token)
    identity = StreamIdentity(user.id, user.role.code)
    db.rollback()  # end the read transaction; the connection goes back to the pool
    return identity


def stream_identity(
    db: DbSession,
    token: Annotated[str | None, Query(description="JWT de acceso")] = None,
) -> StreamIdentity:
    return authenticate_token(db, token)


def format_event(payload: dict[str, Any]) -> str:
    """One SSE frame. ``id`` lets the browser report the last event it saw."""

    lines = []
    if payload.get("id"):
        lines.append(f"id: {payload['id']}")
    lines.append("event: notificacion")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


async def event_stream(
    request: Request,
    identity: StreamIdentity,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    tipos: frozenset[str] | None = None,
) -> AsyncIterator[str]:
    # Subscribed here, inside the running server loop, so the loop captured for
    # call_soon_threadsafe is the one that owns the queue.
    connection: Connection = registry.subscribe(
        identity.user_id, identity.role, asyncio.get_running_loop(), tipos
    )
    try:
        yield f"retry: {RECONNECT_MS}\n: connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(
                    connection.queue.get(), timeout=heartbeat_seconds
                )
            except asyncio.TimeoutError:
                # A comment line: ignored by EventSource, keeps proxies and the
                # tunnel from closing an idle connection.
                yield ": heartbeat\n\n"
                continue
            yield format_event(payload)
    finally:
        registry.unsubscribe(connection)


@router.get(
    "/stream",
    summary="Canal de eventos en tiempo real (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(
    request: Request,
    identity: Annotated[StreamIdentity, Depends(stream_identity)],
    tipos: Annotated[str | None, Query(description=TIPOS_HELP)] = None,
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(request, identity, tipos=parse_tipos(tipos)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# -- WebSocket ---------------------------------------------------------------


def format_ws_event(payload: dict[str, Any]) -> str:
    """The same JSON object as the SSE ``data:`` line, one text frame."""

    return json.dumps(payload, ensure_ascii=False, default=str)


async def _pump_events(
    websocket: WebSocket, connection: Connection, heartbeat_seconds: float
) -> None:
    """Registry queue -> socket, with an application-level ping when idle."""

    while True:
        try:
            payload = await asyncio.wait_for(
                connection.queue.get(), timeout=heartbeat_seconds
            )
        except asyncio.TimeoutError:
            # Cloudflare drops sockets idle for about 100 s: any frame resets it.
            await websocket.send_text(json.dumps({"tipo": "ping"}))
            continue
        await websocket.send_text(format_ws_event(payload))


async def _watch_client(websocket: WebSocket) -> None:
    """Consume (and ignore) whatever the client sends; return when it leaves."""

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/ws")
async def events_ws(
    websocket: WebSocket,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query(description="JWT de acceso")] = None,
    tipos: Annotated[str | None, Query(description=TIPOS_HELP)] = None,
) -> None:
    """Live channel over WebSocket (R17). Refused with close code 4401.

    The handshake is accepted first so the browser receives a real close code
    (a refusal before ``accept`` reaches JavaScript as an anonymous 1006).
    """

    await websocket.accept()
    try:
        # Sync SQLAlchemy work stays off the event loop.
        identity = await run_in_threadpool(authenticate_token, db, token)
    except HTTPException:
        await websocket.close(code=WS_UNAUTHORIZED, reason="unauthorized")
        return

    await serve_ws(websocket, identity, tipos=parse_tipos(tipos))


async def serve_ws(
    websocket: WebSocket,
    identity: StreamIdentity,
    heartbeat_seconds: float | None = None,
    tipos: frozenset[str] | None = None,
) -> None:
    """Subscribe, pump events until either side ends, then always unsubscribe."""

    if heartbeat_seconds is None:
        heartbeat_seconds = HEARTBEAT_SECONDS
    # Subscribed inside the running server loop, so call_soon_threadsafe in the
    # registry targets the loop that owns the queue (same handoff as SSE).
    connection = registry.subscribe(
        identity.user_id, identity.role, asyncio.get_running_loop(), tipos
    )
    tasks = [
        asyncio.ensure_future(_pump_events(websocket, connection, heartbeat_seconds)),
        asyncio.ensure_future(_watch_client(websocket)),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, RuntimeError)):
                logger.warning("WebSocket channel ended with an error: %r", exc)
    finally:
        # No await before the unsubscribe: when the server itself cancels this
        # handler (shutdown, test client), the next await would raise again and
        # the connection would stay in the registry.
        registry.unsubscribe(connection)
        for task in tasks:
            task.cancel()
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except RuntimeError:  # pragma: no cover -- already closed by the peer
                pass
