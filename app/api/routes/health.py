"""Operational endpoints: liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine
from app.services import pacs

router = APIRouter(tags=["operacion"])


@router.get("/health", summary="Estado del servicio")
def health() -> dict[str, str]:
    """Liveness probe.

    Answers without touching the database so it stays meaningful even while the
    database is unreachable: it reports that the process is up, nothing more.
    """

    return {"status": "ok", "environment": get_settings().environment}


@router.get("/health/db", summary="Conectividad con la base de datos")
def health_db() -> dict[str, str]:
    """Readiness probe covering the database connection."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim for diagnosis
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok"}


@router.get("/health/pacs", summary="Conectividad con el servidor de imagenes")
def health_pacs() -> dict[str, str]:
    """Readiness probe for the PACS.

    Kept apart from ``/health`` on purpose: the clinical record must remain
    usable when the imaging server is down, so its availability is reported
    rather than allowed to fail the whole service.
    """

    status = pacs.system_status()
    return {
        "status": "ok" if status.available else "error",
        "detail": status.detail,
    }
