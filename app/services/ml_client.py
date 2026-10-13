"""HTTP client for the AI service.

The service lives in its own container and never touches the database. This
module is the only place that knows its URL. A single client instance is held at
module level so tests can swap in a fake (``set_client``) without any network.

Failures are translated to domain errors: callers decide whether they are fatal
(an analysis request answers 503 and stores nothing) or ignorable (a cleaning
prediction is simply left empty).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.services.errors import MlServiceRejectedError, MlServiceUnavailableError

logger = logging.getLogger(__name__)


class MlClient(Protocol):
    def predict_criticality(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def predict_cleaning_delay(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def models(self) -> dict[str, Any]: ...


class HttpMlClient:
    def __init__(self, base_url: str, timeout: float, cleaning_timeout: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cleaning_timeout = cleaning_timeout

    def _call(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, timeout: float
    ) -> dict[str, Any]:
        try:
            response = httpx.request(
                method, f"{self._base_url}{path}", json=json, timeout=timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("AI service unreachable on %s: %s", path, exc.__class__.__name__)
            raise MlServiceUnavailableError(
                "El servicio de IA no esta disponible"
            ) from exc
        if response.status_code >= 500:
            logger.warning("AI service failed on %s: HTTP %s", path, response.status_code)
            raise MlServiceUnavailableError("El servicio de IA no esta disponible")
        if response.status_code >= 400:
            raise MlServiceRejectedError(
                f"El servicio de IA rechazo los datos enviados (HTTP {response.status_code})"
            )
        return response.json()

    def predict_criticality(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/predict/criticality", json=payload, timeout=self._timeout)

    def predict_cleaning_delay(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST", "/predict/cleaning-delay", json=payload, timeout=self._cleaning_timeout
        )

    def models(self) -> dict[str, Any]:
        return self._call("GET", "/models", timeout=self._timeout)


_client: MlClient | None = None


def get_client() -> MlClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = HttpMlClient(
            settings.ml_service_url,
            settings.ml_timeout_seconds,
            settings.ml_cleaning_timeout_seconds,
        )
    return _client


def set_client(client: MlClient | None) -> None:
    """Replace the client (tests); ``None`` restores the HTTP one."""

    global _client
    _client = client
