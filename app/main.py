"""Application entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.error_handlers import register_error_handlers
from app.api.routes import (
    accounts,
    ai,
    alerts,
    analytics,
    audit,
    auth,
    beds,
    billing,
    cleaning,
    clinical_record,
    cohort,
    integration,
    encounters,
    events,
    fhir_hooks,
    health,
    imaging,
    me,
    notifications,
    observations,
    organizations,
    patients,
    referrals,
)
from app.billing.store import init_billing
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import alert_monitor

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Billing indexes (MongoDB). Never raises: with Mongo down the API starts
    # and the billing routes answer 503 until it is back.
    init_billing()

    # Alert monitor (R20): escalation and the free-but-not-cleaned bed alert.
    # An in-process timer is valid because the API runs ONE worker; tests turn
    # it off (ALERT_ESCALATION_ENABLED=false) and call its functions directly.
    monitor = None
    if settings.alert_escalation_enabled:
        monitor = asyncio.create_task(alert_monitor.monitor_loop(settings))
    try:
        yield
    finally:
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor

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
    lifespan=lifespan,
)

register_error_handlers(app)

def configure_http_security(application: FastAPI, cors_origins: list[str]) -> None:
    """CORS allow-list and defensive headers for every response.

    The UI is served by nginx from the same origin as the API, so CORS is not
    needed in the normal deployment: without configured origins no CORS
    middleware is installed and no ``Access-Control-Allow-Origin`` is ever sent.
    Auth uses an Authorization header, never cookies, hence no credentials.
    """

    if cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # No Content-Security-Policy here: it would break the Swagger UI assets.
    # nginx sets the CSP for the web; these headers are defense in depth.
    @application.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


configure_http_security(app, settings.cors_allowed_origins)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(encounters.router)
app.include_router(observations.router)
app.include_router(organizations.router)
app.include_router(me.router)
app.include_router(audit.router)
app.include_router(accounts.router)
app.include_router(analytics.router)
app.include_router(beds.router)
app.include_router(cleaning.router)
app.include_router(ai.router)
app.include_router(clinical_record.router)
app.include_router(cohort.router)
app.include_router(imaging.router)
app.include_router(integration.router)
app.include_router(fhir_hooks.router)
app.include_router(billing.router)
app.include_router(notifications.router)
app.include_router(referrals.router)
app.include_router(alerts.router)
app.include_router(events.router)

# The web UI (frontend/) is served from the same origin as the API, so one
# public URL exposes both. Routers were registered first: /api/v1/*, /docs,
# /redoc and /health keep winning, and the mount only catches everything else,
# serving index.html at /. The check keeps local runs without the frontend
# folder (e.g. tests) working.
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
