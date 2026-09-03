"""Map service-layer errors to coherent HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.errors import (
    ConflictError,
    FhirGatewayError,
    ForbiddenError,
    NotFoundError,
)


def register_error_handlers(app: FastAPI) -> None:
    """Install exception handlers for the service-layer domain errors."""

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(request: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(FhirGatewayError)
    async def fhir_gateway_handler(
        request: Request, exc: FhirGatewayError
    ) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})
