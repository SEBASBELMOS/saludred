"""Application entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.error_handlers import register_error_handlers
from app.api.routes import (
    audit,
    auth,
    beds,
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

# The web UI is served as static files opened directly from disk (file://),
# so the browser needs CORS to reach the API. Auth uses an Authorization
# header, never cookies: a wildcard origin without credentials is safe here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(encounters.router)
app.include_router(observations.router)
app.include_router(organizations.router)
app.include_router(me.router)
app.include_router(audit.router)
app.include_router(beds.router)
app.include_router(integration.router)

# The web UI (frontend/) is served from the same origin as the API, so one
# public URL exposes both. Routers were registered first: /api/v1/*, /docs,
# /redoc and /health keep winning, and the mount only catches everything else,
# serving index.html at /. The check keeps local runs without the frontend
# folder (e.g. tests) working.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
