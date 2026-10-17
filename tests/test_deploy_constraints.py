"""Deployment facts the event channel depends on (see docs/EVENTOS.md)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_the_api_runs_a_single_worker() -> None:
    """The live-channel registry lives in process memory: with more than one
    worker an event published in one never reaches a stream held by another."""

    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    for name, text in (("Dockerfile", dockerfile), ("entrypoint.sh", entrypoint), ("compose", compose)):
        text = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert not re.search(r"--workers|WEB_CONCURRENCY|replicas|gunicorn", text), name


def test_nginx_does_not_buffer_the_event_stream() -> None:
    conf = (ROOT / "frontend" / "nginx.conf").read_text()
    block = re.search(r"location\s+=\s+/api/v1/events/stream\s*\{(.*?)\n    \}", conf, re.S)
    assert block, "missing dedicated location for the SSE stream"
    body = block.group(1)
    for directive in (
        "proxy_buffering off",
        "proxy_cache off",
        "proxy_read_timeout 1h",
        "X-Accel-Buffering",
        "proxy_http_version 1.1",
    ):
        assert directive in body, directive
    # An exact-match location wins over the generic /api/ regex.
    assert conf.index("/api/v1/events/stream") < conf.index("location ~ ^/(api/")


def test_nginx_upgrades_the_websocket_channel() -> None:
    conf = (ROOT / "frontend" / "nginx.conf").read_text()
    block = re.search(r"location\s+=\s+/api/v1/events/ws\s*\{(.*?)\n    \}", conf, re.S)
    assert block, "missing dedicated location for the WebSocket channel"
    body = block.group(1)
    for directive in (
        "proxy_http_version 1.1",
        "proxy_set_header Upgrade $http_upgrade",
        'proxy_set_header Connection "upgrade"',
        "proxy_read_timeout 1h",
        "proxy_buffering off",
    ):
        assert directive in body, directive
    # limit_req is allowed: it is evaluated once on the handshake. It must not
    # be a stricter zone such as the login one.
    assert "login_per_client" not in body
    # Exact match precedes the generic /api/ regex, which has no Upgrade headers.
    assert conf.index("/api/v1/events/ws") < conf.index("location ~ ^/(api/")


def test_compose_enables_rest_hook_subscriptions_in_hapi() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    assert re.search(r'HAPI_FHIR_SUBSCRIPTION_RESTHOOK_ENABLED:\s*"?true"?', compose)


def _compose_services() -> dict[str, str]:
    """``{service name: its block of text}`` without a YAML parser (comments dropped)."""

    lines = [
        line
        for line in (ROOT / "docker-compose.yml").read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    start = lines.index("services:") + 1
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines[start:]:
        if line and not line.startswith(" "):  # next top-level key (volumes:)
            break
        header = re.match(r"^  ([A-Za-z][\w-]*):\s*$", line)
        if header:
            current = blocks.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(body) for name, body in blocks.items()}


def test_a_plain_compose_up_starts_everything() -> None:
    """R05: one ``docker compose up -d`` raises the whole system. A service with a
    profile would be skipped by it, so no service may declare one."""

    services = _compose_services()
    for name, body in services.items():
        assert not re.search(r"^\s+profiles\s*:", body, re.M), f"{name} uses profiles"
    assert not re.search(r"^\s*profiles\s*:", (ROOT / "docker-compose.yml").read_text(), re.M)


def test_compose_declares_every_required_service() -> None:
    services = _compose_services()
    required = {"api", "db", "mongo", "hapi", "orthanc", "ml", "web"}
    assert required <= set(services), required - set(services)
    tunnels = {name for name, body in services.items() if "cloudflare/cloudflared" in body}
    assert tunnels == {"tunnel-api", "tunnel-fhir", "tunnel-web"}
    # The PACS image is pinned, and the API does not wait for it to start.
    assert re.search(r"image:\s*orthancteam/orthanc:\S+", services["orthanc"])
    api_depends = services["api"].split("depends_on:")[1].split("env_file:")[0]
    assert "orthanc" not in api_depends
    # A tunnel opens only once its target is up.
    assert "condition: service_healthy" in services["tunnel-web"]
    assert "condition: service_healthy" in services["tunnel-api"]


def test_compose_passes_the_ai_settings_with_safe_defaults() -> None:
    api = _compose_services()["api"]
    assert "AI_AUTO_ANALYSIS: ${AI_AUTO_ANALYSIS:-true}" in api
    assert "REQUIRE_REVIEW_NOTE: ${REQUIRE_REVIEW_NOTE:-false}" in api


def _settings(**overrides):  # noqa: ANN202, ANN003
    base = dict(
        database_url="postgresql+psycopg://a:b@h/d",
        jwt_secret_key="j" * 20,
        pseudonym_key="p" * 20,
        fhir_hook_secret="h" * 20,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_hook_secret_is_required_and_long_enough(monkeypatch) -> None:
    monkeypatch.delenv("FHIR_AUTO_SYNC", raising=False)
    monkeypatch.delenv("FHIR_HOOK_SECRET", raising=False)
    assert _settings().fhir_hook_endpoint == "http://api:8000/api/v1/fhir/hooks"
    assert _settings().fhir_auto_sync is True
    with pytest.raises(ValidationError):
        _settings(fhir_hook_secret="short")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_url="postgresql+psycopg://a:b@h/d",
            jwt_secret_key="j" * 20, pseudonym_key="p" * 20,
        )
