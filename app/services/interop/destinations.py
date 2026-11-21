"""Configure the existing sender from the tester's environment convention."""

import os
import re

from app.services.interop import clientes
from app.services.interop.cliente import InteropSender, SenderError

FALSE_VALUES = frozenset({"false", "0", "no", "off"})


def sender_for_partner(db, client_id: str) -> InteropSender:
    """Resolve only registered destinations; HTTP bodies never supply URLs or secrets.

    A partner can name an environment prefix on its registration row. The default
    PROBAR_* configuration is also usable when its client id matches the row.
    Inbound password hashes are never reused as outbound credentials.
    """
    partner = clientes.get_active_client(db, client_id)
    if partner is None:
        raise SenderError("Socio no disponible")
    prefix = partner.outbound_env_prefix
    if prefix is None and os.environ.get("PROBAR_CLIENT_ID") == client_id:
        prefix = "PROBAR"
    if not prefix or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", prefix):
        raise SenderError("Socio sin configuracion de envio")
    values = {key: os.environ.get(f"{prefix}_{key}", "").strip()
              for key in ("BASE_URL", "CLIENT_ID", "CLIENT_SECRET", "SIGNING_SECRET", "API_KEY", "SIGN")}
    # Any-partner mode: ``<PREFIX>_API_KEY`` replaces the client id and secret (the key is sent
    # verbatim as X-Api-Key and no token is requested); ``<PREFIX>_SIGN=false`` skips the HMAC
    # signature, and then the signing secret is not needed either. The defaults are unchanged.
    api_key = values["API_KEY"] or None
    sign = values["SIGN"].lower() not in FALSE_VALUES
    required = ["BASE_URL"]
    if api_key is None:
        required += ["CLIENT_ID", "CLIENT_SECRET"]
    if sign:
        required.append("SIGNING_SECRET")
    if not all(values[key] for key in required):
        raise SenderError("Configuracion de envio incompleta")
    return InteropSender(
        values["BASE_URL"], values["CLIENT_ID"], values["CLIENT_SECRET"], values["SIGNING_SECRET"],
        scopes=(clientes.SCOPE_BUNDLE, clientes.SCOPE_TASK), retries=0, api_key=api_key, sign=sign,
    )
