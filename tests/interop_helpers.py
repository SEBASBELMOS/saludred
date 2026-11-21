"""Shared pieces of the interoperability tests: partner credentials, tokens, signed posts."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.services.interop import clientes, firma
from app.services.interop.cliente import (
    SCOPE_OBSERVATION,
    SCOPE_SERVICE_REQUEST,
    build_observation,
    build_service_request,
    encode,
)
from app.services.interop.federado import id_federado

# Test-only secret (not the professor's, not a real one).
FEDERATED_SECRET = "unit-test-federated-secret-not-real"
BOTH = (SCOPE_OBSERVATION, SCOPE_SERVICE_REQUEST)


@dataclass
class Creds:
    client_id: str
    client_secret: str
    signing_secret: str


def register(db, client_id: str = "equipo1", scopes=BOTH) -> Creds:  # noqa: ANN001
    registered = clientes.register_client(
        db, client_id=client_id, name=f"Equipo {client_id}", scopes=list(scopes), allow_plaintext=True
    )
    return Creds(client_id, registered.client_secret, registered.signing_secret)


def token_request(http, creds: Creds, scope: str | None = None, **overrides: str):  # noqa: ANN001
    data = {
        "grant_type": "client_credentials",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    if scope is not None:
        data["scope"] = scope
    data.update(overrides)
    return http.post("/oauth/token", data=data)


def get_token(http, creds: Creds, scope: str | None = None) -> str:  # noqa: ANN001
    response = token_request(http, creds, scope)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def observation(tipo: str, numero: str, value: float = 182, code: str = "2339-0", **kw: Any) -> dict:
    system = get_settings().federated_system
    return build_observation(code, value, tipo, numero, system=system, secret=FEDERATED_SECRET, **kw)


def referral(tipo: str, numero: str, motivo: str = "Control de diabetes tipo 2", **kw: Any) -> dict:
    system = get_settings().federated_system
    return build_service_request(motivo, tipo, numero, system=system, secret=FEDERATED_SECRET, **kw)


def federated(tipo: str, numero: str) -> str:
    return id_federado(tipo, numero, secret=FEDERATED_SECRET)


def signed_post(  # noqa: PLR0913
    http,  # noqa: ANN001
    path: str,
    resource: dict | bytes,
    creds: Creds,
    token: str | None,
    *,
    now: float | None = None,
    nonce: str | None = None,
    sign_with: str | None = None,
    send_body: bytes | None = None,
    drop: tuple[str, ...] = (),
):
    """POST a message signed like the sender does. ``send_body`` differs from the signed one
    to simulate tampering; ``drop`` removes headers."""

    body = resource if isinstance(resource, bytes) else encode(resource)
    headers = {"Content-Type": "application/fhir+json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(firma.sign(sign_with or creds.signing_secret, body, now=now, nonce=nonce))
    for name in drop:
        headers.pop(name, None)
    return http.post(path, content=send_body if send_body is not None else body, headers=headers)


def outcome_issues(response) -> list[dict]:  # noqa: ANN001
    body = response.json()
    assert body["resourceType"] == "OperationOutcome", body
    return body["issue"]


def now() -> float:
    return time.time()


def dumps(resource: dict) -> bytes:
    return json.dumps(resource).encode()
