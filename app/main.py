"""Application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine

settings = get_settings()

app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    description=(
        "API de coordinacion de camas hospitalarias en una red EPS/IPS, "
        "con exposicion de la informacion clinica como recursos HL7 FHIR R4."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get("/health", tags=["operacion"], summary="Estado del servicio")
def health() -> dict[str, str]:
    """Liveness probe.

    Answers without touching the database so it stays meaningful even while the
    database is unreachable: it reports that the process is up, nothing more.
    """

    return {"status": "ok", "environment": settings.environment}


@app.get("/health/db", tags=["operacion"], summary="Conectividad con la base de datos")
def health_db() -> dict[str, str]:
    """Readiness probe covering the database connection."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim for diagnosis
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok"}
