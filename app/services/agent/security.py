"""Security events of the agent and the temporary IP block.

Normal mode writes two logs: errors (``app.services.agent.*``) and security events
(logger ``app.security``: kind, level, source, user, IP, action taken; the time is
the log record's). The same event is an ``AGENT_SECURITY_EVENT`` audit row with the
caller's IP in ``audit_log.ip_address``. The text of the message is never logged.

The block list is in-process on purpose: the API runs with a single worker
(docker-compose.yml), so there is no second process to disagree with. It is lost on
restart; that is documented as a residual risk in docs/AGENTE.md.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import AuditAction
from app.models.identity import User
from app.services import trail
from app.services.errors import commit

security_log = logging.getLogger("app.security")
ENTITY = "agent"

# Kinds of event.
INJECTION_MESSAGE = "inyeccion_en_mensaje"
INJECTION_TOOL = "inyeccion_en_herramienta"
OFF_TOPIC = "fuera_de_alcance"
BLOCKED_REQUEST = "solicitud_bloqueada"


def _now() -> float:
    return time.monotonic()


def block_key(user: object | None, ip: str | None) -> str | None:
    """Who a strike is charged to: the authenticated user id, else the address.

    Behind nginx / the tunnel many clinicians can share one socket address, so blocking
    by address would lock the whole clinic out for one user's abuse. The IP stays in the
    audit row and the security log.
    """

    user_id = getattr(user, "id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{ip}" if ip else None


class StrikeBlockList:
    """Strikes per key (a user, see ``block_key``) inside a sliding window; at ``strikes``
    the key is blocked for ``minutes``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._strikes: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._strikes.clear()
            self._blocked_until.clear()

    def remaining(self, key: str | None) -> int:
        """Seconds left of the block of ``key`` (0: not blocked)."""

        if not key:
            return 0
        with self._lock:
            until = self._blocked_until.get(key)
            if until is None:
                return 0
            left = until - _now()
            if left <= 0:
                del self._blocked_until[key]
                return 0
            return int(left) + 1

    def strike(self, key: str | None) -> bool:
        """Count one strike. ``True`` when this strike starts a block."""

        settings = get_settings()
        if not key or settings.agent_ip_block_minutes <= 0:
            return False
        window = settings.agent_ip_block_minutes * 60
        now = _now()
        with self._lock:
            if key in self._blocked_until and self._blocked_until[key] > now:
                return False
            marks = self._strikes[key]
            while marks and now - marks[0] > window:
                marks.popleft()
            marks.append(now)
            if len(marks) < settings.agent_ip_block_strikes:
                return False
            marks.clear()
            self._blocked_until[key] = now + window
            return True


blocklist = StrikeBlockList()


def log_event(
    *, kind: str, level: str, source: str, username: str | None, ip: str | None, action: str,
    patterns: tuple[str, ...] = (),
) -> None:
    security_log.warning(
        "evento_de_seguridad tipo=%s nivel=%s fuente=%s usuario=%s ip=%s accion=%s patrones=%s",
        kind, level, source, username or "-", ip or "-", action, ",".join(patterns) or "-",
    )


def record_event(
    db: Session,
    user: User | None,
    *,
    kind: str,
    level: str,
    source: str,
    action: str,
    ip: str | None,
    patterns: tuple[str, ...] = (),
    patient_id: object | None = None,
    conversation: str | None = None,
) -> None:
    """Log the event and write its audit row (never the text that caused it)."""

    log_event(kind=kind, level=level, source=source, username=user.username if user else None,
              ip=ip, action=action, patterns=patterns)
    trail.audit(
        db,
        action=AuditAction.AGENT_SECURITY_EVENT,
        entity_type=ENTITY,
        entity_id=None,
        actor=user,
        metadata={
            "tipo": kind,
            "nivel": level,
            "fuente": source,
            "accion": action,
            "patrones": list(patterns),
            "patient_id": str(patient_id) if patient_id else None,
            "conversation": conversation,
        },
        ip_address=ip,
    )
    commit(db)


def strike_and_maybe_block(
    db: Session, user: User | None, *, reason: str, ip: str | None, conversation: str | None = None
) -> bool:
    """Add a strike for the user (address if anonymous); when it starts a block, audit
    ``IP_BLOCKED`` (the action keeps its name; the row carries the IP) and log it."""

    if not blocklist.strike(block_key(user, ip)):
        return False
    settings = get_settings()
    log_event(kind="ip_bloqueada", level="alto", source="agente", username=user.username if user else None,
              ip=ip, action=f"bloqueo_{settings.agent_ip_block_minutes}_min")
    trail.audit(
        db,
        action=AuditAction.IP_BLOCKED,
        entity_type=ENTITY,
        entity_id=None,
        actor=user,
        metadata={
            "motivo": reason,
            "minutos": settings.agent_ip_block_minutes,
            "intentos": settings.agent_ip_block_strikes,
            "alcance": "/api/v1/agente/*",
            "conversation": conversation,
        },
        ip_address=ip,
    )
    commit(db)
    return True
