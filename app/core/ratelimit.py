"""Sliding-window rate limit per client IP (STRIDE: denial of service).

Nginx already limits what enters through the ``web`` container, but the partners'
traffic reaches FastAPI through ``tunnel-api`` and the published port 8000 without
passing through it. The limit therefore lives here, in the application.

``LimitadorVentana`` keeps, per key, the instants of the requests inside the window.
A request is allowed while fewer than ``maximo`` instants remain in the last
``ventana_segundos``; otherwise it is refused and the caller is told how long until
the oldest one leaves the window (``Retry-After``). It is a *sliding* window: unlike a
fixed counter it cannot be bursted twice around a boundary.

The strict groups (login, /oauth/token, /interop/*, /agente/*) are keyed by the address
AND who is asking (username, client_id, verified token subject, user id), because behind
nginx / the tunnel callers may share one socket address; a per-address floor (10x the cap)
still bounds a flood of changing identities. ``general`` stays per address.

State is in the process: valid because the API runs ONE worker (docker-compose.yml).
With several workers each would count on its own.
"""

from __future__ import annotations

import base64
import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.core.net import client_ip

# Beyond this many keys, idle ones are dropped so memory cannot grow without bound
# under a spray of addresses.
MAX_KEYS = 10_000

# Paths that get the strict cap, each group with its own counter (so a busy agent
# user cannot lock their own login). Matched by prefix, so the routes of later phases
# (/interop/*, /oauth/token, the /api/v1 aliases) are covered the day they appear.
STRICT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("login", ("/api/v1/auth/login",)),
    ("oauth", ("/oauth/token",)),
    ("interop", ("/interop/", "/api/v1/interop/")),
    ("agente", ("/api/v1/agente/",)),
)
# Never limited: liveness probes and the long-lived live channels (SSE and WebSocket).
EXEMPT_PREFIXES: tuple[str, ...] = ("/health", "/api/v1/events/")


# One limiter per (group, cap, window): changing a setting starts a fresh counter.
_LIMITERS: dict[tuple[str, int, float], "LimitadorVentana"] = {}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: float = 0.0


class LimitadorVentana:
    """At most ``maximo`` events per ``ventana_segundos`` for each key."""

    def __init__(
        self,
        maximo: int,
        ventana_segundos: float,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maximo = maximo
        self.ventana = ventana_segundos
        self._reloj = reloj
        self._marcas: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permitir(self, clave: str) -> Decision:
        now = self._reloj()
        limit = now - self.ventana
        with self._lock:
            marcas = self._marcas.get(clave)
            if marcas is None:
                if len(self._marcas) >= MAX_KEYS:
                    self._purge(limit)
                marcas = self._marcas[clave] = deque()
            while marcas and marcas[0] <= limit:
                marcas.popleft()
            if len(marcas) >= self.maximo:
                return Decision(False, max(marcas[0] + self.ventana - now, 0.0))
            marcas.append(now)
            return Decision(True)

    def _purge(self, limit: float) -> None:
        for key in [k for k, q in self._marcas.items() if not q or q[-1] <= limit]:
            del self._marcas[key]


def classify(path: str) -> str | None:
    """The counter a path belongs to: a strict group, ``general``, or ``None`` (exempt)."""

    if any(path == p.rstrip("/") or path.startswith(p) for p in EXEMPT_PREFIXES):
        return None
    for name, prefixes in STRICT_GROUPS:
        if any(path == p.rstrip("/") or path.startswith(p) for p in prefixes):
            return name
    return "general"


# Strict groups whose bucket is the address PLUS who is asking. Behind nginx / the tunnel
# every caller can share one socket address (CF-Connecting-IP is only trusted when told
# to), so an address-only bucket lets one noisy partner or user lock out all the others.
IDENTITY_GROUPS = frozenset({"login", "oauth", "interop", "agente"})
# The per-address floor on top of the identity bucket: IP_FLOOR_FACTOR x the strict cap.
IP_FLOOR_FACTOR = 10
MAX_PEEK_BYTES = 16_384  # the body is only inspected for a login / token request this small
MAX_IDENTITY_CHARS = 80


def _norm(value: object) -> str:
    return value.strip().casefold()[:MAX_IDENTITY_CHARS] if isinstance(value, str) else ""


async def _peek_body(request: Request, receive: Receive) -> tuple[bytes | None, Receive]:
    """Up to ``MAX_PEEK_BYTES`` of the body, plus a ``receive`` that replays it.

    ``None`` when the body is larger or unfinished: the caller then keys by address
    only. Nothing is lost for the application: the buffered messages are replayed.
    """

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_PEEK_BYTES:
        return None, receive
    buffered: list[dict] = []
    size = 0
    complete = False
    while True:
        message = await receive()
        buffered.append(message)
        if message["type"] != "http.request":
            break
        size += len(message.get("body", b""))
        if size > MAX_PEEK_BYTES:
            break
        if not message.get("more_body", False):
            complete = True
            break

    pending = list(buffered)

    async def replay() -> dict:
        if pending:
            return pending.pop(0)
        return await receive()

    body = b"".join(m.get("body", b"") for m in buffered) if complete else None
    return body, replay


def _bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


def _verified_subject(group: str, token: str | None) -> str:
    """``sub`` of a Bearer token whose SIGNATURE verifies, else ``""``.

    It is only a bucket key (real authentication happens in the route), but it must be
    verified: an unverified ``sub`` would let a caller pick a fresh bucket per request.
    """

    if not token:
        return ""
    from app.core.security import decode_access_token

    if group in ("interop",):
        from app.services.interop import clientes

        claims = clientes.decode_system_token(token)
        if claims is not None:
            return f"sys:{_norm(claims['sub'])}"
    claims = decode_access_token(token)
    return f"usr:{_norm(claims['sub'])}" if claims is not None else ""


def _form_or_json(body: bytes, content_type: str) -> dict:
    if "json" in content_type.lower():
        try:
            data = json.loads(body)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    parsed = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


async def _identity(group: str, request: Request, receive: Receive) -> tuple[str, Receive]:
    """Who is asking, for the bucket key, and a ``receive`` that still yields the body.

    * login   -> the normalized username of the body (the lockout also counts per user);
    * oauth   -> ``client_id`` (form, JSON or HTTP Basic), bounded;
    * interop -> the ``sub`` of a verified system token (or user token); without a verified
      Bearer, the client id part of ``X-Api-Key`` (``apikey:<client id>``, UNVERIFIED like the
      oauth group: the per-address floor still bounds a caller that rotates ids);
    * agente  -> the user id of a verified user token.
    Anything missing or unparsable gives ``""`` and the bucket degrades to the address.
    """

    if group in ("interop", "agente"):
        subject = _verified_subject(group, _bearer(request))
        if not subject and group == "interop":
            client_id = _norm(request.headers.get("x-api-key", "").partition(":")[0])
            if client_id:
                subject = f"apikey:{client_id}"
        return subject, receive
    body, receive = await _peek_body(request, receive)
    content_type = request.headers.get("content-type", "")
    data = _form_or_json(body, content_type) if body else {}
    if group == "login":
        return _norm(data.get("username")), receive
    client_id = _norm(data.get("client_id"))
    if not client_id:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "basic" and supplied:
            try:
                client_id = _norm(base64.b64decode(supplied).decode("utf-8").partition(":")[0])
            except ValueError:
                client_id = ""
    return client_id, receive


class RateLimitMiddleware:
    """Pure ASGI middleware: it only looks at HTTP requests (WebSockets pass through)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _limiter(group: str, scale: int = 1) -> LimitadorVentana:
        settings = get_settings()
        if group == "general":
            maximo = settings.rate_limit_general_max_requests
        else:
            maximo = settings.rate_limit_max_requests * scale
        ventana = settings.rate_limit_window_seconds
        key = (f"{group}#{scale}" if scale != 1 else group, maximo, ventana)
        limiter = _LIMITERS.get(key)
        if limiter is None:
            limiter = _LIMITERS[key] = LimitadorVentana(maximo, ventana)
        return limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not get_settings().rate_limit_enabled:
            await self.app(scope, receive, send)
            return
        group = classify(scope["path"])
        if group is None:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        ip = client_ip(request) or "desconocida"
        if group in IDENTITY_GROUPS:
            identity, receive = await _identity(group, request, receive)
            decision = self._limiter(group).permitir(f"{group}:{ip}|{identity}")
            if decision.allowed:
                # Anti-spray floor: a flood of ever-changing identities from one address
                # still hits a (generous) per-address cap.
                decision = self._limiter(group, scale=IP_FLOOR_FACTOR).permitir(f"{group}:{ip}")
        else:
            decision = self._limiter(group).permitir(f"{group}:{ip}")
        if decision.allowed:
            await self.app(scope, receive, send)
            return

        wait = max(1, math.ceil(decision.retry_after))
        body = json.dumps(
            {"detail": f"Demasiadas peticiones. Intente de nuevo en {wait} s."}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"retry-after", str(wait).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def reset() -> None:
    """Forget every counter (tests; also what a restart does)."""

    _LIMITERS.clear()
