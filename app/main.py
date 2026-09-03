"""Application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.routes import (
    audit,
    auth,
    integration,
    encounters,
    health,
    me,
    observations,
    organizations,
    patients,
)
from app.core.config import get_settings

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

register_error_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(encounters.router)
app.include_router(observations.router)
app.include_router(organizations.router)
app.include_router(me.router)
app.include_router(audit.router)
app.include_router(integration.router)
