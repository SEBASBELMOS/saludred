"""Operational endpoints: liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine

router = APIRouter(tags=["operacion"])


@router.get("/", summary="Indice del servicio")
def index() -> dict[str, object]:
    """Punto de entrada del servicio.

    Sin esta ruta, abrir la URL base devuelve un 404 que parece una caida del
    servicio cuando en realidad esta sano. Quien llegue aqui obtiene el nombre
    del sistema y donde continuar.
    """

    return {
        "servicio": get_settings().project_name,
        "version": "0.1.0",
        "estado": "operativo",
        "documentacion": "/docs",
        "esquema_openapi": "/openapi.json",
        "salud": "/health",
        "salud_base_datos": "/health/db",
        "api": get_settings().api_v1_prefix,
    }


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
