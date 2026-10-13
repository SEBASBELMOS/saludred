"""Receiver of the HAPI Subscription (R21, rest-hook channel).

How HAPI calls (verified in ``SubscriptionDeliveringRestHookSubscriber``, HAPI
7.4): with a payload in the Subscription, a created or updated resource is
delivered by a FHIR ``update`` against the endpoint taken as if it were a FHIR
base, i.e. ``PUT {endpoint}/Observation/{id}``; a deletion is
``DELETE {endpoint}/Observation/{id}``. A plain ``POST`` is also accepted
(other servers, Postman, ``curl``).

This is NOT a user endpoint: no JWT. The caller proves itself with the shared
secret HAPI sends in the ``Authorization: Bearer`` header (the value of
``FHIR_HOOK_SECRET`` stored in the Subscription's ``channel.header``), compared
in constant time. Wrong or missing secret: 401. The response is a small
``OperationOutcome`` with 200, so HAPI does not retry a delivery that was
handled (including echoes, duplicates and unknown patients).
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import DbSession
from app.core.config import get_settings
from app.services import fhir_inbound
from app.services.errors import InvalidValueError

router = APIRouter(prefix="/api/v1/fhir/hooks", tags=["FHIR rest-hook"])

FHIR_JSON = "application/fhir+json"


def require_hook_secret(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().fhir_hook_secret
    scheme, _, supplied = (authorization or "").partition(" ")
    ok = scheme.lower() == "bearer" and secrets.compare_digest(
        supplied.strip().encode(), expected.encode()
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clave del hook invalida",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _outcome(message: str, code: int = status.HTTP_200_OK) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        media_type=FHIR_JSON,
        content={
            "resourceType": "OperationOutcome",
            "issue": [
                {"severity": "information", "code": "informational", "diagnostics": message}
            ],
        },
    )


async def _read_resource(request: Request) -> dict[str, Any] | None:
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        body = await request.json()
    except ValueError as exc:
        raise InvalidValueError("El cuerpo no es JSON valido") from exc
    if not isinstance(body, dict):
        raise InvalidValueError("El cuerpo debe ser un recurso FHIR (objeto JSON)")
    return body


# The handler is async only to read the body; the database work runs in the
# thread pool so a slow query never stalls the event loop that serves the SSE
# streams.
def _handle(db: DbSession, resource: dict[str, Any] | None, path_id: str | None) -> JSONResponse:
    if resource is None:
        return _outcome("Sin payload: nada que procesar")
    result = fhir_inbound.handle_observation(db, resource, path_id=path_id)
    messages = {
        "processed": "Observation procesada",
        "unknown_patient": "Paciente desconocido: evento auditado, sin datos creados",
        "duplicate": "Observation ya procesada (misma version)",
        "echo": "Observation enviada por SaludRed: ignorada",
    }
    return _outcome(messages[result.outcome])


@router.put(
    "/Observation/{resource_id}",
    dependencies=[Depends(require_hook_secret)],
    summary="Entrega de HAPI (update del payload sobre el endpoint)",
)
async def deliver_put(db: DbSession, request: Request, resource_id: str) -> JSONResponse:
    return await run_in_threadpool(_handle, db, await _read_resource(request), resource_id)


@router.post(
    "/Observation",
    dependencies=[Depends(require_hook_secret)],
    summary="Entrega de una Observation por POST",
)
@router.post(
    "/observation",
    dependencies=[Depends(require_hook_secret)],
    include_in_schema=False,
)
async def deliver_post(db: DbSession, request: Request) -> JSONResponse:
    return await run_in_threadpool(_handle, db, await _read_resource(request), None)


@router.delete(
    "/Observation/{resource_id}",
    dependencies=[Depends(require_hook_secret)],
    summary="Aviso de borrado en HAPI (no se hace nada)",
)
async def deliver_delete(resource_id: str) -> JSONResponse:
    # A deletion on the FHIR server does not delete clinical history here.
    return _outcome(f"Borrado de Observation/{resource_id} ignorado")
