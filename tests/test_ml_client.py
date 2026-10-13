"""The HTTP client turns every failure of the AI service into a domain error."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.errors import MlServiceRejectedError, MlServiceUnavailableError
from app.services.ml_client import HttpMlClient


def _client() -> HttpMlClient:
    return HttpMlClient("http://ml:8001/", timeout=3.0, cleaning_timeout=1.0)


def _reply(monkeypatch, status: int, body: dict | None = None, *, record: list | None = None):
    def fake(method, url, json=None, timeout=None):  # noqa: ANN001
        if record is not None:
            record.append((method, url, json, timeout))
        return httpx.Response(status, json=body or {}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", fake)


def test_success_returns_the_json_and_uses_the_right_url_and_timeout(monkeypatch) -> None:
    calls: list = []
    _reply(monkeypatch, 200, {"probability": 0.5}, record=calls)
    assert _client().predict_criticality({"pseudonym": "pz-0123456789abcdef"}) == {"probability": 0.5}
    assert _client().predict_cleaning_delay({}) == {"probability": 0.5}
    assert _client().models() == {"probability": 0.5}
    assert [(m, u, t) for m, u, _j, t in calls] == [
        ("POST", "http://ml:8001/predict/criticality", 3.0),
        ("POST", "http://ml:8001/predict/cleaning-delay", 1.0),
        ("GET", "http://ml:8001/models", 3.0),
    ]


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_network_failures_are_unavailable(monkeypatch, exc) -> None:
    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise exc

    monkeypatch.setattr(httpx, "request", boom)
    with pytest.raises(MlServiceUnavailableError):
        _client().predict_criticality({})


def test_server_errors_are_unavailable_and_client_errors_are_rejections(monkeypatch) -> None:
    _reply(monkeypatch, 503)
    with pytest.raises(MlServiceUnavailableError):
        _client().predict_criticality({})
    _reply(monkeypatch, 422)
    with pytest.raises(MlServiceRejectedError):
        _client().predict_criticality({})


def test_the_pseudonym_key_is_required_and_long_enough(monkeypatch) -> None:
    base = {"database_url": "sqlite://", "jwt_secret_key": "x" * 20}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pseudonym_key="short", **base)
    monkeypatch.delenv("PSEUDONYM_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **base)
    assert Settings(_env_file=None, pseudonym_key="k" * 16, **base).ml_service_url == "http://ml:8001"
