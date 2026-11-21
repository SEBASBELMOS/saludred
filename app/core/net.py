"""Client address of a request: one helper, so every module reads the IP the same way."""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

from app.core.config import get_settings

CF_HEADER = "cf-connecting-ip"


def client_ip(request: Request) -> str | None:
    """The address of the caller.

    ``CF-Connecting-IP`` is honoured only when ``TRUST_CF_CONNECTING_IP`` is true
    (the API is reachable solely through Cloudflare): anywhere else the header is
    attacker-controlled. A header that is not an IP address is ignored. Without
    it, the address of the socket.
    """

    if get_settings().trust_cf_connecting_ip:
        header = request.headers.get(CF_HEADER, "").strip()
        if header:
            try:
                return str(ipaddress.ip_address(header))
            except ValueError:
                pass
    client = request.client
    return client.host[:64] if client and client.host else None
