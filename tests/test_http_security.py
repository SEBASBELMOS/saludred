"""CORS allow-list, defensive headers and authentication on every route."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app, configure_http_security

EVIL = "https://evil.example"
GOOD = "https://app.example"


def _app(origins: list[str]) -> FastAPI:
    application = FastAPI()

    @application.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    configure_http_security(application, origins)
    return application


def test_cors_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_allowed_origins == []

    client = TestClient(_app([]))
    response = client.get("/ping", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options(
        "/ping", headers={"Origin": EVIL, "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_the_real_app_does_not_reflect_an_unknown_origin() -> None:
    response = TestClient(app).get("/health", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in response.headers


def test_cors_env_is_parsed_robustly(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f" {GOOD} , ,https://b.example,")
    assert Settings(_env_file=None).cors_allowed_origins == [GOOD, "https://b.example"]


def test_only_listed_origins_get_the_cors_header() -> None:
    client = TestClient(_app([GOOD]))
    allowed = client.get("/ping", headers={"Origin": GOOD})
    assert allowed.headers["access-control-allow-origin"] == GOOD
    unknown = client.get("/ping", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in unknown.headers


def test_security_headers_on_api_responses() -> None:
    for response in (
        TestClient(app).get("/api/v1/auth/me"),  # 401 also carries them
        TestClient(_app([])).get("/ping"),
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "content-security-policy" not in response.headers


# Routes under /api/v1 that answer without a token, with the reason.
PUBLIC_ROUTES = {("POST", "/api/v1/auth/login"): "it is how a token is obtained"}


def _fill(path: str) -> str:
    """Replace every path parameter with a random UUID (all of them are UUIDs)."""

    while "{" in path:
        start, end = path.index("{"), path.index("}")
        path = path[:start] + str(uuid.uuid4()) + path[end + 1:]
    return path


def test_every_api_route_requires_a_token() -> None:
    client = TestClient(app)
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in PUBLIC_ROUTES:
                continue
            response = client.request(method, _fill(route.path))
            assert response.status_code in (401, 403), (method, route.path, response.status_code)
            checked += 1
    assert checked > 60
