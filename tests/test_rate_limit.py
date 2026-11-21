"""Sliding-window rate limit per IP: 10 requests per 10 seconds on the sensitive routes."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jose import jwt

from app.core.security import create_access_token

from app.core import ratelimit
from app.core.config import get_settings
from app.core.ratelimit import LimitadorVentana, RateLimitMiddleware, classify
from app.main import app


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def limiter_on(monkeypatch):  # noqa: ANN001, ANN201
    """These tests turn the limiter ON (conftest leaves it off) and start from zero."""

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    ratelimit.reset()
    yield
    ratelimit.reset()


# -- The window ---------------------------------------------------------------


def test_ten_requests_pass_and_the_eleventh_waits() -> None:
    clock = Clock()
    limiter = LimitadorVentana(10, 10, reloj=clock)
    assert all(limiter.permitir("ip").allowed for _ in range(10))
    refused = limiter.permitir("ip")
    assert refused.allowed is False
    assert refused.retry_after == pytest.approx(10.0)


def test_the_window_slides_it_does_not_reset_all_at_once() -> None:
    clock = Clock()
    limiter = LimitadorVentana(10, 10, reloj=clock)
    for _ in range(10):
        assert limiter.permitir("ip").allowed
        clock.now += 1  # one request per second: t = 0..9
    # t = 10: the request of t=0 just left the window (the boundary is exclusive).
    assert limiter.permitir("ip").allowed
    # The window now holds t=1..9 plus t=10: full again, the next must wait for t=1.
    refused = limiter.permitir("ip")
    assert refused.allowed is False
    assert refused.retry_after == pytest.approx(1.0)
    clock.now += 1
    assert limiter.permitir("ip").allowed


def test_after_the_window_everything_is_allowed_again() -> None:
    clock = Clock()
    limiter = LimitadorVentana(10, 10, reloj=clock)
    for _ in range(11):
        limiter.permitir("ip")
    clock.now += 10
    assert all(limiter.permitir("ip").allowed for _ in range(10))


def test_each_key_has_its_own_window() -> None:
    limiter = LimitadorVentana(2, 10, reloj=Clock())
    assert limiter.permitir("a").allowed and limiter.permitir("a").allowed
    assert not limiter.permitir("a").allowed
    assert limiter.permitir("b").allowed


def test_refused_requests_do_not_extend_the_wait() -> None:
    clock = Clock()
    limiter = LimitadorVentana(2, 10, reloj=clock)
    limiter.permitir("a")
    limiter.permitir("a")
    for _ in range(50):
        limiter.permitir("a")  # hammering while blocked
    clock.now += 10
    assert limiter.permitir("a").allowed


def test_idle_keys_are_dropped_when_the_table_is_full(monkeypatch) -> None:
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 3)
    clock = Clock()
    limiter = LimitadorVentana(1, 10, reloj=clock)
    for key in ("a", "b", "c"):
        limiter.permitir(key)
    clock.now += 20  # all of them idle
    limiter.permitir("d")
    assert set(limiter._marcas) == {"d"}


# -- Which routes get which cap ----------------------------------------------


@pytest.mark.parametrize(
    "path, group",
    [
        ("/api/v1/auth/login", "login"),
        ("/oauth/token", "oauth"),
        ("/interop/Observation", "interop"),
        ("/interop/ServiceRequest", "interop"),
        ("/api/v1/interop/pendientes", "interop"),
        ("/api/v1/agente/chat", "agente"),
        ("/api/v1/agente/decidir", "agente"),
        ("/api/v1/patients", "general"),
        ("/api/v1/auth/me", "general"),
        ("/", "general"),
        ("/health", None),
        ("/health/db", None),
        ("/api/v1/events/stream", None),
        ("/api/v1/events/ws", None),
    ],
)
def test_routes_are_classified_by_prefix(path: str, group: str | None) -> None:
    assert classify(path) == group


# -- The middleware -----------------------------------------------------------


def _app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(RateLimitMiddleware)

    @application.post("/oauth/token")
    def token() -> dict:
        return {"ok": True}

    @application.post("/api/v1/auth/login")
    async def login(request: Request) -> dict:
        return {"body": (await request.body()).decode()}  # proves the body is replayed intact

    @application.post("/interop/Observation")
    def interop() -> dict:
        return {"ok": True}

    @application.post("/api/v1/agente/chat")
    def agent() -> dict:
        return {"ok": True}

    @application.get("/api/v1/other")
    def other() -> dict:
        return {"ok": True}

    @application.get("/health")
    def health() -> dict:
        return {"ok": True}

    return application


def test_a_burst_of_fifteen_gives_ten_200_and_five_429() -> None:
    client = TestClient(_app())
    codes = [client.post("/oauth/token").status_code for _ in range(15)]
    assert codes == [200] * 10 + [429] * 5


def test_the_429_tells_how_long_to_wait() -> None:
    client = TestClient(_app())
    for _ in range(10):
        client.post("/oauth/token")
    refused = client.post("/oauth/token")
    assert refused.status_code == 429
    wait = int(refused.headers["retry-after"])
    assert 1 <= wait <= 10
    assert "Demasiadas peticiones" in refused.json()["detail"]


def test_groups_do_not_share_a_counter() -> None:
    client = TestClient(_app())
    for _ in range(12):
        client.post("/oauth/token")
    assert client.get("/api/v1/other").status_code == 200  # general cap, untouched


def test_health_is_never_limited() -> None:
    client = TestClient(_app())
    assert [client.get("/health").status_code for _ in range(40)] == [200] * 40


def test_the_general_cap_is_configurable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_general_max_requests", 3)
    client = TestClient(_app())
    assert [client.get("/api/v1/other").status_code for _ in range(5)] == [200, 200, 200, 429, 429]


def test_the_strict_cap_is_configurable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_max_requests", 2)
    client = TestClient(_app())
    assert [client.post("/oauth/token").status_code for _ in range(3)] == [200, 200, 429]


def test_disabled_means_no_limit(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    client = TestClient(_app())
    assert {client.post("/oauth/token").status_code for _ in range(30)} == {200}


def test_each_client_ip_has_its_own_window_only_when_cloudflare_is_trusted(monkeypatch) -> None:
    client = TestClient(_app())

    # Not trusted (default): the header is forgeable, every request is the socket's address.
    for number in range(10):
        client.post("/oauth/token", headers={"CF-Connecting-IP": f"203.0.113.{number}"})
    forged = client.post("/oauth/token", headers={"CF-Connecting-IP": "203.0.113.200"})
    assert forged.status_code == 429

    ratelimit.reset()
    monkeypatch.setattr(get_settings(), "trust_cf_connecting_ip", True)
    for _ in range(10):
        client.post("/oauth/token", headers={"CF-Connecting-IP": "198.51.100.1"})
    assert client.post("/oauth/token", headers={"CF-Connecting-IP": "198.51.100.1"}).status_code == 429
    assert client.post("/oauth/token", headers={"CF-Connecting-IP": "198.51.100.2"}).status_code == 200


# -- The bucket is the address AND who is asking (review finding 3) -----------
#
# Behind nginx / the tunnel every caller can arrive from one socket address, so an
# address-only bucket would let one noisy caller lock out everybody else.


def _system_token(client_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    claims = {
        "sub": client_id, "scope": "system/Observation.write", "iss": settings.jwt_issuer,
        "aud": settings.interop_audience, "iat": now, "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(claims, settings.interop_signing_key, algorithm="HS256")


def _codes(client: TestClient, path: str, count: int, **kwargs) -> list[int]:  # noqa: ANN003
    return [client.post(path, **kwargs).status_code for _ in range(count)]


def test_oauth_is_keyed_by_client_id_so_partners_do_not_share_a_bucket() -> None:
    client = TestClient(_app())
    assert _codes(client, "/oauth/token", 15, data={"client_id": "equipo1"}) == [200] * 10 + [429] * 5
    assert _codes(client, "/oauth/token", 10, data={"client_id": "equipo2"}) == [200] * 10
    assert client.post("/oauth/token", data={"client_id": "equipo2"}).status_code == 429
    # JSON bodies and HTTP Basic name the client too.
    assert _codes(client, "/oauth/token", 3, json={"client_id": "equipo3"}) == [200] * 3
    basic = {"authorization": "Basic " + base64.b64encode(b"equipo4:secret").decode()}
    assert _codes(client, "/oauth/token", 10, headers=basic) == [200] * 10
    assert client.post("/oauth/token", headers=basic).status_code == 429
    # The same client written differently (case, padding) is the same bucket.
    assert client.post("/oauth/token", data={"client_id": "EQUIPO4 "}).status_code == 429


def test_login_is_keyed_by_normalized_username_and_the_body_still_arrives() -> None:
    client = TestClient(_app())
    first = client.post("/api/v1/auth/login", json={"username": "Ana", "password": "x"})
    assert first.status_code == 200 and "Ana" in first.json()["body"] and "password" in first.json()["body"]
    codes = _codes(client, "/api/v1/auth/login", 14, json={"username": " ANA ", "password": "x"})
    assert codes == [200] * 9 + [429] * 5  # "Ana" and " ANA " are the same user: 10 in all
    assert _codes(client, "/api/v1/auth/login", 10, json={"username": "luis", "password": "x"}) == [200] * 10


def test_a_flood_of_changing_identities_still_hits_the_per_address_floor() -> None:
    client = TestClient(_app())
    codes = [
        client.post("/oauth/token", data={"client_id": f"c{number}"}).status_code for number in range(105)
    ]
    assert codes == [200] * 100 + [429] * 5  # IP_FLOOR_FACTOR x the strict cap


def test_interop_is_keyed_by_the_verified_token_subject() -> None:
    client = TestClient(_app())
    one = {"authorization": f"Bearer {_system_token('equipo1')}"}
    two = {"authorization": f"Bearer {_system_token('equipo2')}"}
    assert _codes(client, "/interop/Observation", 15, headers=one) == [200] * 10 + [429] * 5
    assert _codes(client, "/interop/Observation", 10, headers=two) == [200] * 10


def test_an_unverifiable_token_cannot_pick_its_own_bucket() -> None:
    client = TestClient(_app())
    forged = [
        client.post("/interop/Observation", headers={"authorization": f"Bearer forged-{n}"}).status_code
        for n in range(12)
    ]
    assert forged == [200] * 10 + [429] * 2  # all fall back to the address bucket
    other_key = jwt.encode({"sub": "equipo9", "aud": get_settings().interop_audience}, "not-the-key", algorithm="HS256")
    assert client.post("/interop/Observation", headers={"authorization": f"Bearer {other_key}"}).status_code == 429


def test_the_agent_is_keyed_by_the_authenticated_user() -> None:
    client = TestClient(_app())
    first = {"authorization": f"Bearer {create_access_token(uuid.uuid4(), 'MEDICO')}"}
    second = {"authorization": f"Bearer {create_access_token(uuid.uuid4(), 'MEDICO')}"}
    assert _codes(client, "/api/v1/agente/chat", 15, headers=first) == [200] * 10 + [429] * 5
    assert _codes(client, "/api/v1/agente/chat", 10, headers=second) == [200] * 10
    assert client.post("/api/v1/agente/chat").status_code == 200  # anonymous: the address bucket


def test_a_body_larger_than_the_peek_limit_is_keyed_by_address_and_still_delivered() -> None:
    client = TestClient(_app())
    big = '{"username": "ana", "pad": "' + "x" * 20_000 + '"}'
    response = client.post("/api/v1/auth/login", content=big, headers={"content-type": "application/json"})
    assert response.status_code == 200 and response.json()["body"] == big


# -- The real application -----------------------------------------------------


def test_the_real_login_route_is_limited_and_the_429_keeps_the_security_headers() -> None:
    client = TestClient(app)
    # An invalid body is answered 422 before touching the database; it still counts.
    codes = [client.post("/api/v1/auth/login", json={}).status_code for _ in range(15)]
    assert codes == [422] * 10 + [429] * 5
    refused = client.post("/api/v1/auth/login", json={})
    assert refused.headers["retry-after"]
    assert refused.headers["x-content-type-options"] == "nosniff"
    assert client.get("/health").status_code == 200  # never limited


def test_the_real_app_limits_future_partner_and_agent_paths_by_prefix() -> None:
    client = TestClient(app)
    for path in ("/oauth/token", "/interop/Observation", "/api/v1/agente/chat"):
        codes = [client.post(path, json={}).status_code for _ in range(12)]
        assert codes[10:] == [429, 429], path
        assert 429 not in codes[:10], path
