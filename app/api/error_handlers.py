"""Map service-layer errors to coherent HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.errors import (
    BillingUnavailableError,
    ConflictError,
    FhirGatewayError,
    ForbiddenError,
    InvalidValueError,
    MlServiceRejectedError,
    MlServiceUnavailableError,
    NotFoundError,
    PacsUnavailableError,
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

    @app.exception_handler(PacsUnavailableError)
    async def pacs_handler(request: Request, exc: PacsUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(InvalidValueError)
    async def invalid_value_handler(request: Request, exc: InvalidValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(MlServiceUnavailableError)
    async def ml_unavailable_handler(
        request: Request, exc: MlServiceUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "El servicio de IA no esta disponible en este momento. "
                "No se creo ningun reporte; intente de nuevo mas tarde."
            },
        )

    @app.exception_handler(MlServiceRejectedError)
    async def ml_rejected_handler(
        request: Request, exc: MlServiceRejectedError
    ) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(BillingUnavailableError)
    async def billing_unavailable_handler(
        request: Request, exc: BillingUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "La contabilidad no esta disponible en este momento. "
                "Intente de nuevo mas tarde; el resto del sistema sigue funcionando."
            },
        )
