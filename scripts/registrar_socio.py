"""Register a partner system for the interoperability exchange.

    python -m scripts.registrar_socio --client-id equipo3 --nombre "Equipo 3" \
        --alcances system/Observation.write system/ServiceRequest.write

Creates the ``interop_clients`` row and prints the three credentials that the partner
needs, ONCE:

* ``client_id`` and ``client_secret``  -> ``POST /oauth/token``
* ``secreto_de_firma``                 -> the HMAC of every message (different from the
  client secret, slide 33)

Hand them over by a private channel, never in the group chat. Only the Argon2id hash of
the client secret is stored; the signing secret is stored ENCRYPTED with the field keys
(``FIELD_ENCRYPTION_KEYS``). Without keys the script refuses, unless you accept the risk
of keeping the signing secret readable in the database with ``--permitir-texto-plano``.

The script writes to the database the API is configured with (DATABASE_URL): it is run
on purpose, once per partner. ``--listar`` only shows the registered clients.
"""

from __future__ import annotations

import argparse
import sys

from app.services.errors import ConflictError, InvalidValueError
from app.services.interop import clientes


def main(argv: list[str] | None = None, session_factory=None, out=print) -> int:  # noqa: ANN001
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--client-id", help="identificador del socio (minusculas, 3 a 64 caracteres)")
    parser.add_argument("--nombre", default="", help="nombre legible del socio")
    parser.add_argument(
        "--alcances", nargs="+", default=list(clientes.KNOWN_SCOPES),
        help="alcances que se le conceden (por defecto los dos de escritura)",
    )
    parser.add_argument("--permitir-texto-plano", action="store_true",
                        help="guardar el secreto de firma sin cifrar si no hay llaves (riesgo aceptado)")
    parser.add_argument("--prefijo-envio", default=None,
                        help="prefijo de las variables de entorno con que SaludRed ENVIA a este socio "
                             "(ej. SOCIO_A: SOCIO_A_BASE_URL, _CLIENT_ID, _CLIENT_SECRET, _SIGNING_SECRET)")
    parser.add_argument("--listar", action="store_true", help="solo listar los socios registrados")
    args = parser.parse_args(argv)

    if session_factory is None:
        from app.core.database import SessionLocal

        session_factory = SessionLocal

    with session_factory() as db:
        if args.listar:
            for client in clientes.list_clients(db):
                state = "activo" if client.is_active else "inactivo"
                out(f"{client.client_id:30} {state:9} {' '.join(client.scopes)}")
            return 0
        if not args.client_id:
            parser.error("--client-id es obligatorio")
        try:
            registered = clientes.register_client(
                db,
                client_id=args.client_id,
                name=args.nombre or args.client_id,
                scopes=clientes.parse_scopes(args.alcances),
                allow_plaintext=args.permitir_texto_plano,
                outbound_env_prefix=args.prefijo_envio,
            )
        except (InvalidValueError, ConflictError) as exc:
            out(f"No se registro: {exc}")
            return 1

    out("Socio registrado. Entregue estos datos por un canal privado: NO se muestran otra vez.")
    out(f"  client_id         : {registered.client.client_id}")
    out(f"  client_secret     : {registered.client_secret}")
    out(f"  secreto_de_firma  : {registered.signing_secret}")
    out(f"  alcances          : {' '.join(registered.client.scopes)}")
    if registered.client.signing_secret.startswith(clientes.PLAIN_PREFIX):
        out("  AVISO: el secreto de firma quedo en texto plano en la base (sin FIELD_ENCRYPTION_KEYS).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
