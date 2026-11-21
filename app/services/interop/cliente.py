"""SaludRed as the SENDER of a partner exchange (slides 28-30).

Steps of one delivery::

    1. POST {base}/oauth/token                 (client_id, client_secret, scope)  -> JWT 10 min
    2. sign the RAW body: HMAC(secreto_firma, fecha.nonce.cuerpo)
    3. POST {base}/interop/Observation|ServiceRequest   Bearer + three X-Interop-* headers
    4. handle the answer code

How each code is handled (the code tells the sender what to fix, slide 30):

====  ========================================================================
201   delivered and associated to a patient.
202   delivered; the partner still has to reconcile the patient.
401   the token expired or is invalid: get a NEW token once and send again. A
      second 401 means the credentials or the signing secret are wrong.
403   the token is valid but lacks the scope: report which one; resending does
      not help (the partner must register it).
409   already delivered (repeated message): treated as delivered.
422   does not meet the profile: the ``OperationOutcome`` says what to correct.
429   rate limited: wait ``Retry-After`` and send again.
5xx   partner failure: retry with a growing wait.
====  ========================================================================

Every attempt is signed again with a fresh date and nonce (a nonce is single use).
The patient travels as the federated id (``federado``), never as a document.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.interop import federado, firma
from app.services.interop.perfil import LOINC, PROFILE, SNOMED, UCUM

SCOPE_OBSERVATION = "system/Observation.write"
SCOPE_SERVICE_REQUEST = "system/ServiceRequest.write"
FHIR_JSON = "application/fhir+json"
API_KEY_HEADER = "X-Api-Key"

MAX_WAIT_SECONDS = 15.0


@dataclass
class SendResult:
    """What happened to one message, in terms a person can read in a table."""

    recurso: str
    status: int
    entregado: bool
    mensaje: str
    intentos: int = 1
    issues: list[dict[str, Any]] = field(default_factory=list)


class SenderError(RuntimeError):
    """The message could not even be attempted (no token, no federated secret...).

    ``status`` is the HTTP code of the failed token request, or 0 when there was none.
    """

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Message builders (FHIR R4, common profile)
# ---------------------------------------------------------------------------


def _subject(tipo: str, numero: str, secret: str | None, system: str) -> dict[str, Any]:
    try:
        value = federado.id_federado(tipo, numero, secret=secret)
    except federado.FederatedDisabledError:
        raise SenderError(
            "FEDERATED_SECRET no esta configurado: sin el no se puede calcular el "
            "identificador federado del paciente y no se envia nada"
        ) from None
    return {"identifier": {"system": system, "value": value}}


def build_observation(
    code: str, value: float, tipo: str, numero: str, *, system: str, secret: str | None = None,
    resource_id: str | None = None, observed_at: datetime | None = None,
) -> dict[str, Any]:
    """An Observation of the common profile (LOINC code, UCUM unit, federated subject)."""

    profile = PROFILE[code]
    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": LOINC, "code": code, "display": profile.name}]},
        "subject": _subject(tipo, numero, secret, system),
        "effectiveDateTime": (observed_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "valueQuantity": {
            "value": value, "unit": profile.unit, "system": UCUM, "code": profile.unit,
        },
    }
    if resource_id:
        resource["id"] = resource_id
    return resource


def build_service_request(
    motivo: str, tipo: str, numero: str, *, system: str, secret: str | None = None,
    organization_code: str | None = None, resource_id: str | None = None,
    reason_snomed: str | None = None,
) -> dict[str, Any]:
    """A referral ServiceRequest (SNOMED 3457005). ``organization_code`` names the target IPS."""

    reason: dict[str, Any] = {"text": motivo}
    if reason_snomed:
        reason["coding"] = [{"system": SNOMED, "code": reason_snomed}]
    resource: dict[str, Any] = {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "code": {"coding": [{"system": SNOMED, "code": "3457005", "display": "Patient referral"}]},
        "subject": _subject(tipo, numero, secret, system),
        "authoredOn": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reasonCode": [reason],
    }
    if organization_code:
        resource["performer"] = [{"identifier": {"value": organization_code}}]
    if resource_id:
        resource["id"] = resource_id
    return resource


def encode(resource: dict[str, Any]) -> bytes:
    """The exact bytes that are signed AND sent (the receiver signs the raw body)."""

    return json.dumps(resource, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# The sender
# ---------------------------------------------------------------------------


class InteropSender:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        signing_secret: str,
        *,
        http: httpx.Client | None = None,
        scopes: tuple[str, ...] = (SCOPE_OBSERVATION, SCOPE_SERVICE_REQUEST),
        retries: int = 2,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        api_key: str | None = None,
        sign: bool = True,
    ) -> None:
        """``api_key`` (any-partner mode) is sent verbatim as ``X-Api-Key`` and replaces the
        token request; ``sign=False`` omits the three HMAC headers for a partner that does
        not verify them. Both default to the strict behaviour."""

        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self._client_secret = client_secret
        self._signing_secret = signing_secret
        self._api_key = (api_key or "").strip() or None
        self._sign = sign
        self.scopes = scopes
        self.retries = retries
        self._sleep = sleep
        self._clock = clock
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._token: str | None = None
        self._token_until = 0.0
        self.granted_scope: str = ""

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- step 1: token ----------------------------------------------------------

    def obtener_token(self, *, force: bool = False) -> str:
        if self._token and not force and self._clock() < self._token_until:
            return self._token
        try:
            response = self._request(
                "POST", "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "scope": " ".join(self.scopes),
                },
            )
        except httpx.HTTPError as exc:
            raise SenderError(f"Sin respuesta del socio al pedir el token: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            error = _safe_json(response)
            raise SenderError(
                f"No se obtuvo el token ({response.status_code} "
                f"{error.get('error', '')}): {error.get('error_description', 'revise client_id y client_secret')}",
                response.status_code,
            )
        # A 200 that is not a token response (an HTML login page, a proxy error, a body
        # without access_token) is a transport failure like any other, never a crash.
        try:
            body = response.json()
            token = body["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError("empty access_token")
            lifetime = max(30, int(body.get("expires_in", 600)) - 30)
            scope = str(body.get("scope", ""))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SenderError(
                f"El socio respondio al pedir el token con un cuerpo no valido ({exc.__class__.__name__})"
            ) from exc
        self._token = token
        self._token_until = self._clock() + lifetime
        self.granted_scope = scope
        return self._token

    # -- steps 2 to 4: sign, send, handle the code ---------------------------------

    def enviar_observacion(self, resource: dict[str, Any]) -> SendResult:
        return self._enviar("Observation", resource)

    def enviar_remision(self, resource: dict[str, Any]) -> SendResult:
        return self._enviar("ServiceRequest", resource)

    def enviar_bundle(self, resource: dict[str, Any]) -> SendResult:
        """Transport for envio.send_to_partner, which enforces scope and consent."""
        return self._enviar("Bundle", resource)

    def enviar_task(self, resource: dict[str, Any]) -> SendResult:
        return self._enviar("Task", resource)

    def _enviar(self, resource_type: str, resource: dict[str, Any]) -> SendResult:
        body = encode(resource)
        refreshed = False
        attempts = 0
        while True:
            attempts += 1
            headers = {"Content-Type": FHIR_JSON}
            if self._api_key is not None:
                headers[API_KEY_HEADER] = self._api_key  # no token request in this mode
            else:
                headers["Authorization"] = f"Bearer {self.obtener_token()}"
            if self._sign:
                headers.update(firma.sign(self._signing_secret, body, now=self._clock()))
            try:
                response = self._request("POST", f"/interop/{resource_type}", content=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempts <= self.retries:
                    self._sleep(min(MAX_WAIT_SECONDS, 2.0 * attempts))
                    continue
                return SendResult(resource_type, 0, False, f"Sin respuesta del socio: {exc.__class__.__name__}", attempts)

            status = response.status_code
            outcome = _safe_json(response)
            issues = [i for i in outcome.get("issue", []) if isinstance(i, dict)]
            detail = "; ".join(str(i.get("diagnostics", "")) for i in issues if i.get("diagnostics"))

            if status in (201, 202) or (status == 200 and resource_type in ("Task", "Bundle")):
                return SendResult(resource_type, status, True, detail or "Entregado", attempts, issues)
            if status == 409 and resource_type not in ("Task", "Bundle"):
                return SendResult(resource_type, status, True, "Ya entregado (mensaje repetido): " + detail, attempts, issues)
            if status == 401 and self._api_key is not None:
                return SendResult(resource_type, status, False,
                                  "401 con la API key: revise la clave configurada para el socio. " + detail,
                                  attempts, issues)
            if status == 401 and not refreshed:
                # The token may have expired: ask for a new one ONCE and send again.
                refreshed = True
                self._token = None
                self.obtener_token(force=True)
                continue
            if status == 401:
                return SendResult(resource_type, status, False,
                                  "401 tras renovar el token: revise las credenciales y el secreto de firma. " + detail,
                                  attempts, issues)
            if status == 403:
                return SendResult(resource_type, status, False,
                                  f"Falta el alcance en el socio (token con: {self.granted_scope or 'ninguno'}). "
                                  "Reintentar no ayuda: el socio debe registrarlo. " + detail,
                                  attempts, issues)
            if status == 422:
                return SendResult(resource_type, status, False, "No cumple el perfil: " + detail, attempts, issues)
            if status == 429 and attempts <= self.retries:
                self._sleep(min(MAX_WAIT_SECONDS, _retry_after(response)))
                continue
            if status >= 500 and attempts <= self.retries:
                self._sleep(min(MAX_WAIT_SECONDS, 2.0 * attempts))
                continue
            return SendResult(resource_type, status, False, detail or f"Respuesta inesperada {status}", attempts, issues)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._http.request(method, self.base_url + path, **kwargs)


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(1.0, float(response.headers.get("retry-after", "1")))
    except ValueError:
        return 1.0


def audit_sent(db: Any, result: SendResult, *, base_url: str, client_id: str, ip: str | None = None) -> None:
    """Write ``INTEROP_SENT`` for one delivery (what was sent, to whom, which code)."""

    from app.models.enums import AuditAction
    from app.services import trail

    trail.audit(
        db,
        action=AuditAction.INTEROP_SENT,
        entity_type="interop",
        entity_id=None,
        actor=None,
        metadata={
            "origen": "saludred",
            "destino": base_url,
            "cliente": client_id,
            "recurso": result.recurso,
            "codigo_http": result.status,
            "entregado": result.entregado,
            "intentos": result.intentos,
        },
        ip_address=ip,
    )
    db.commit()
