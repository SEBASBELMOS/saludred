"""Pair tester of the interoperability exchange (slide 33: "correr el tester").

Sends THREE live messages to a partner's base URL with the credentials the partner gave
you, and prints one row per step with the code the partner answered:

    1. token OAuth2 (client credentials)
    2. glucose 182 mg/dL        (normal)         expects 201 or 202
    3. glucose 310 mg/dL        (critical)       expects 201 or 202
    4. referral (ServiceRequest SNOMED 3457005)  expects 201 or 202

    PROBAR_BASE_URL=https://equipo3.example PROBAR_CLIENT_ID=... PROBAR_CLIENT_SECRET=... \
    PROBAR_SIGNING_SECRET=... PROBAR_DOC_TYPE=CC PROBAR_DOC_NUMBER=1144123456 \
    FEDERATED_SECRET=... python -m scripts.probar_sistema

``--self`` points it at SaludRed itself (default base ``http://localhost:8000``), with a
second registered client (``python -m scripts.registrar_socio``). ``--negativos`` also
produces, on purpose, the refusals the receiver must give: 401 (no token), 401 (altered
body), 403 (missing scope), 409 (repeated message), 422 (misspelled LOINC system) and 202
(unknown patient). Every row says whether the code was the expected one; the exit code is
non-zero if any was not.

Secrets come ONLY from the environment (never from arguments, which land in the shell
history) and are never printed. Environment:

    PROBAR_BASE_URL, PROBAR_CLIENT_ID, PROBAR_CLIENT_SECRET, PROBAR_SIGNING_SECRET
    PROBAR_DOC_TYPE, PROBAR_DOC_NUMBER      the test patient (known to the receiver)
    PROBAR_ORG_CODE                         optional: code of the target IPS of the referral
    PROBAR2_CLIENT_ID / _CLIENT_SECRET / _SIGNING_SECRET
                                            --negativos: a client registered ONLY with
                                            system/Observation.write (for the 403)
    FEDERATED_SECRET, FEDERATED_SYSTEM      the agreed secret and the shared system URI
                                            (PROBAR_FEDERATED_* take precedence)

``--remision-ida-vuelta`` (R30) adds the referral round trip: a valid referral Bundle (201/202),
one whose last entry is invalid (422, nothing stored) and a Task for a referral this client never
received (404). When ``PROBAR_MEDICO_USER/_PASSWORD``, ``PROBAR_ESPECIALISTA_USER/_PASSWORD`` and
``PROBAR_PACIENTE_ID`` are set it also runs the full loop through the staff API (consent, referral,
specialist accepts, the doctor sees the partner's answer). With no partner, ``PROBAR_BASE_URL`` is
our own public URL and ``PROBAR_CLIENT_ID`` is our own system registered as a partner with the
Bundle and Task scopes. The client needs ``system/Bundle.write`` and ``system/Task.write``.

Each delivery is audited as ``INTEROP_SENT`` in the database this process is configured
with; ``--sin-auditoria`` skips it (and it is skipped by itself when there is no database).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.interop import cliente, clientes, federado, firma, roundtrip
from app.services.interop.cliente import InteropSender, SenderError

DEFAULT_SELF_URL = "http://localhost:8000"


@dataclass
class Row:
    paso: str
    esperado: str
    codigo: str
    ok: bool
    detalle: str = ""


def _federated(env: Mapping[str, str]) -> tuple[str | None, str]:
    secret = env.get("PROBAR_FEDERATED_SECRET") or env.get("FEDERATED_SECRET") or None
    system = env.get("PROBAR_FEDERATED_SYSTEM") or env.get("FEDERATED_SYSTEM") or ""
    if not system or secret is None:
        try:  # fall back to the application settings (inside the api container)
            from app.core.config import get_settings

            settings = get_settings()
            system = system or settings.federated_system
            secret = secret or settings.federated_secret_value
        except Exception:  # noqa: BLE001 -- no full environment: the caller reports it
            system = system or "urn:uao:salud-digital:paciente-federado"
    return secret, system


def _short(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(rows: list[Row], out: Callable[[str], None]) -> None:
    widths = (3, 46, 9, 7, 9)
    head = ("#", "Paso", "Esperado", "Codigo", "Resultado")
    out("  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True)))
    out("  ".join("-" * w for w in widths))
    for i, row in enumerate(rows, 1):
        cells = (str(i), _short(row.paso, widths[1]), row.esperado, row.codigo, "OK" if row.ok else "FALLA")
        out("  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)))
        if row.detalle and not row.ok:
            out(f"     -> {_short(row.detalle, 110)}")


def _post_raw(
    http: httpx.Client, base: str, path: str, body: bytes, headers: dict[str, str],
    sleep: Callable[[float], None],
) -> httpx.Response:
    """One POST; a 429 (our own rate limit) is waited out and sent again, up to twice."""

    for attempt in range(3):
        response = http.post(base + path, content=body, headers=headers)
        if response.status_code != 429 or attempt == 2:
            return response
        sleep(min(15.0, max(1.0, float(response.headers.get("retry-after", "1")))))
    return response  # pragma: no cover


def _delivered(code: int) -> bool:
    return code in (201, 202)


def run_pair(
    sender: InteropSender, env: Mapping[str, str], rows: list[Row], audit: Callable[[cliente.SendResult], None]
) -> None:
    secret, system = _federated(env)
    tipo = env.get("PROBAR_DOC_TYPE", "CC")
    numero = env.get("PROBAR_DOC_NUMBER", "")
    org = env.get("PROBAR_ORG_CODE") or None

    try:
        sender.obtener_token()
        rows.append(Row("Token OAuth2 (client credentials)", "200", "200", True))
    except SenderError as exc:
        rows.append(Row("Token OAuth2 (client credentials)", "200", str(exc.status or "-"), False, str(exc)))
        return

    stamp = uuid.uuid4().hex[:8]
    steps: list[tuple[str, Callable[[], cliente.SendResult]]] = [
        ("Glucosa 182 mg/dL (normal)", lambda: sender.enviar_observacion(
            cliente.build_observation("2339-0", 182, tipo, numero, system=system, secret=secret,
                                      resource_id=f"prueba-normal-{stamp}"))),
        ("Glucosa 310 mg/dL (critica)", lambda: sender.enviar_observacion(
            cliente.build_observation("2339-0", 310, tipo, numero, system=system, secret=secret,
                                      resource_id=f"prueba-critica-{stamp}"))),
        ("Remision (ServiceRequest 3457005)", lambda: sender.enviar_remision(
            cliente.build_service_request(
                "Prueba de interoperabilidad: control de diabetes tipo 2", tipo, numero,
                system=system, secret=secret, organization_code=org,
                resource_id=f"prueba-remision-{stamp}", reason_snomed="44054006"))),
    ]
    for name, send in steps:
        try:
            result = send()
        except SenderError as exc:
            rows.append(Row(name, "201/202", "-", False, str(exc)))
            continue
        rows.append(Row(name, "201/202", str(result.status or "-"), _delivered(result.status), result.mensaje))
        audit(result)


def run_negatives(
    http: httpx.Client, sender: InteropSender, env: Mapping[str, str], base: str,
    rows: list[Row], sleep: Callable[[float], None],
) -> None:
    secret, system = _federated(env)
    tipo = env.get("PROBAR_DOC_TYPE", "CC")
    numero = env.get("PROBAR_DOC_NUMBER", "")
    signing = env["PROBAR_SIGNING_SECRET"]
    stamp = uuid.uuid4().hex[:8]

    def message(value: float, suffix: str) -> dict[str, Any]:
        return cliente.build_observation("2339-0", value, tipo, numero, system=system, secret=secret,
                                         resource_id=f"negativo-{suffix}-{stamp}")

    def expect(name: str, expected: int, code: int, detail: str = "") -> None:
        rows.append(Row(name, str(expected), str(code), code == expected, detail))

    try:
        token = sender.obtener_token()
    except SenderError as exc:
        rows.append(Row("Negativos: token", "200", str(exc.status or "-"), False, str(exc)))
        return
    bearer = {"Authorization": f"Bearer {token}", "Content-Type": cliente.FHIR_JSON}

    # 401: no token at all
    body = cliente.encode(message(182, "sin-token"))
    response = _post_raw(http, base, "/interop/Observation", body,
                         {"Content-Type": cliente.FHIR_JSON, **firma.sign(signing, body)}, sleep)
    expect("Sin token", 401, response.status_code)

    # 401: body changed after signing
    body = cliente.encode(message(182, "alterado"))
    headers = {**bearer, **firma.sign(signing, body)}
    tampered = body.replace(b"182", b"82")
    response = _post_raw(http, base, "/interop/Observation", tampered, headers, sleep)
    expect("Cuerpo alterado tras firmar", 401, response.status_code)

    # 401: a message signed an hour ago
    body = cliente.encode(message(182, "viejo"))
    headers = {**bearer, **firma.sign(signing, body, now=time.time() - 3600)}
    response = _post_raw(http, base, "/interop/Observation", body, headers, sleep)
    expect("Mensaje firmado hace una hora", 401, response.status_code)

    # 409: the very same message twice (same nonce)
    body = cliente.encode(message(182, "repetido"))
    headers = {**bearer, **firma.sign(signing, body)}
    first = _post_raw(http, base, "/interop/Observation", body, headers, sleep)
    rows.append(Row("Mensaje original (antes del reenvio)", "201/202", str(first.status_code),
                    _delivered(first.status_code)))
    response = _post_raw(http, base, "/interop/Observation", body, headers, sleep)
    expect("Mismo mensaje reenviado (nonce repetido)", 409, response.status_code)

    # 422: LOINC system misspelled
    resource = message(182, "loinc")
    resource["code"]["coding"][0]["system"] = "http://loinc.com"
    body = cliente.encode(resource)
    response = _post_raw(http, base, "/interop/Observation", body, {**bearer, **firma.sign(signing, body)}, sleep)
    outcome = response.json() if response.headers.get("content-type", "").startswith("application/fhir") else {}
    expect("Sistema LOINC mal escrito", 422, response.status_code,
           json.dumps(outcome.get("issue", [])[:1], ensure_ascii=False))

    # 202: a patient the receiver does not know
    unknown = cliente.build_observation(
        "2339-0", 182, "CC", f"9{uuid.uuid4().int % 10**9:09d}", system=system, secret=secret,
        resource_id=f"negativo-desconocido-{stamp}")
    body = cliente.encode(unknown)
    response = _post_raw(http, base, "/interop/Observation", body, {**bearer, **firma.sign(signing, body)}, sleep)
    expect("Paciente desconocido (cola de conciliacion)", 202, response.status_code)

    # 403: a client registered only with Observation.write sends a ServiceRequest
    second = env.get("PROBAR2_CLIENT_ID")
    if not second:
        rows.append(Row("Alcance faltante (segundo cliente)", "403", "-", False,
                        "omitido: defina PROBAR2_CLIENT_ID, PROBAR2_CLIENT_SECRET y PROBAR2_SIGNING_SECRET"))
        return
    other = InteropSender(
        base, second, env.get("PROBAR2_CLIENT_SECRET", ""), env.get("PROBAR2_SIGNING_SECRET", ""),
        http=http, scopes=(cliente.SCOPE_OBSERVATION, cliente.SCOPE_SERVICE_REQUEST), sleep=sleep,
    )
    try:
        result = other.enviar_remision(cliente.build_service_request(
            "Prueba de alcance", tipo, numero, system=system, secret=secret,
            resource_id=f"negativo-alcance-{stamp}"))
        expect("Alcance faltante (cliente solo Observation)", 403, result.status, result.mensaje)
    except SenderError as exc:
        rows.append(Row("Alcance faltante (cliente solo Observation)", "403", str(exc.status or "-"), False, str(exc)))


def run_round_trip(
    sender: InteropSender, env: Mapping[str, str], rows: list[Row],
    audit: Callable[[cliente.SendResult], None],
) -> None:
    """R30, receiving side: a Bundle the receiver must accept, one it must refuse, and a Task
    for a referral it never sent (404). Needs the Bundle and Task scopes on the client."""

    secret, system = _federated(env)
    tipo = env.get("PROBAR_DOC_TYPE", "CC")
    numero = env.get("PROBAR_DOC_NUMBER", "")
    stamp = uuid.uuid4().hex[:8]
    try:
        federated_id = federado.id_federado(tipo, numero, secret=secret)
    except (federado.FederatedIdError, federado.FederatedDisabledError) as exc:
        rows.append(Row("Ida y vuelta: identificador federado", "-", "-", False, str(exc)))
        return
    identifier = {"system": system, "value": federated_id}

    def bundle(request_id: str, *extra: dict[str, Any]) -> dict[str, Any]:
        order = roundtrip.build_referral_request(
            request_id, "Prueba de ida y vuelta (R30)", "Medicina interna", "rutina")
        order["subject"] = {"identifier": identifier}
        observation = cliente.build_observation(
            "2339-0", 182, tipo, numero, system=system, secret=secret, resource_id=f"{request_id}-obs")
        resources = [{"resourceType": "Patient", "identifier": [identifier]}, order, observation, *extra]
        return {"resourceType": "Bundle", "type": "collection",
                "entry": [{"resource": r} for r in resources]}

    bad_observation = cliente.build_observation(
        "2339-0", 5000, tipo, numero, system=system, secret=secret, resource_id=f"prueba-invalida-{stamp}")
    task = {"resourceType": "Task", "status": "accepted", "intent": "order",
            "focus": {"identifier": {"system": roundtrip.REQUEST_SYSTEM,
                                     "value": f"prueba-desconocida-{stamp}"}}}
    steps: list[tuple[str, tuple[int, ...], Callable[[], cliente.SendResult]]] = [
        ("Bundle de remision valido", (201, 202),
         lambda: sender.enviar_bundle(bundle(f"prueba-bundle-{stamp}"))),
        ("Bundle con ultima entrada invalida (nada se guarda)", (422,),
         lambda: sender.enviar_bundle(bundle(f"prueba-bundle-malo-{stamp}", bad_observation))),
        ("Task de una remision que no enviamos", (404,), lambda: sender.enviar_task(task)),
    ]
    for name, expected, send in steps:
        try:
            result = send()
        except SenderError as exc:
            rows.append(Row(name, "/".join(map(str, expected)), "-", False, str(exc)))
            continue
        rows.append(Row(name, "/".join(map(str, expected)), str(result.status or "-"),
                        result.status in expected, result.mensaje))
        audit(result)


def run_staff_round_trip(
    http: httpx.Client, base: str, env: Mapping[str, str], rows: list[Row],
    sleep: Callable[[float], None],
) -> None:
    """R30, full loop through the staff API: the doctor refers, the specialist answers and the
    doctor sees the partner's answer. With no partner, ``base`` is our own public URL and
    ``PROBAR_CLIENT_ID`` is our own system registered as a partner (the server needs the
    ``PROBAR_*`` variables to send). Skipped unless the staff accounts are in the environment."""

    needed = ("PROBAR_MEDICO_USER", "PROBAR_MEDICO_PASSWORD", "PROBAR_ESPECIALISTA_USER",
              "PROBAR_ESPECIALISTA_PASSWORD", "PROBAR_PACIENTE_ID")
    missing = [name for name in needed if not env.get(name)]
    name = "Remision de ida y vuelta por la API del personal"
    if missing:
        rows.append(Row(name, "-", "-", False, "omitido: defina " + ", ".join(missing)))
        return

    def login(user_key: str, password_key: str) -> dict[str, str] | None:
        response = http.post(base + "/api/v1/auth/login",
                             json={"username": env[user_key], "password": env[password_key]})
        if response.status_code != 200:
            return None
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    doctor = login("PROBAR_MEDICO_USER", "PROBAR_MEDICO_PASSWORD")
    specialist = login("PROBAR_ESPECIALISTA_USER", "PROBAR_ESPECIALISTA_PASSWORD")
    if doctor is None or specialist is None:
        rows.append(Row(name, "-", "-", False, "no se pudo iniciar sesion con las cuentas indicadas"))
        return
    client_id = env["PROBAR_CLIENT_ID"]
    patient_id = env["PROBAR_PACIENTE_ID"]
    stamp = uuid.uuid4().hex[:8]

    consent = http.post(base + "/api/v1/consents", headers=doctor, json={
        "paciente_id": patient_id, "cliente_interop_id": client_id,
        "vigente_desde": datetime.now(timezone.utc).isoformat()})
    rows.append(Row("Consentimiento para el destino", "201/409", str(consent.status_code),
                    consent.status_code in (201, 409)))
    sent = http.post(base + "/api/v1/interop/remisiones", headers=doctor, json={
        "paciente_id": patient_id, "cliente_interop_id": client_id,
        "motivo": f"Prueba de ida y vuelta {stamp}", "servicio": "Medicina interna",
        "prioridad": "rutina"})
    rows.append(Row("Remision enviada al socio", "201", str(sent.status_code),
                    sent.status_code == 201 and sent.json().get("estado") == "enviada",
                    sent.text[:100] if sent.status_code != 201 else ""))
    if sent.status_code != 201:
        return
    request_id = sent.json()["solicitud_id"]

    inbox = http.get(base + "/api/v1/referrals", headers=specialist, params={"rol": "recibidas"})
    mine = [r for r in (inbox.json() if inbox.status_code == 200 else [])
            if stamp in r.get("motivo", "")]
    rows.append(Row("Remision en la bandeja del especialista", "1", str(len(mine)), len(mine) == 1))
    if len(mine) != 1:
        return
    accepted = http.post(f"{base}/api/v1/referrals/{mine[0]['id']}/accept", headers=specialist)
    rows.append(Row("El especialista acepta", "200", str(accepted.status_code), accepted.status_code == 200))

    state = ""
    for _ in range(3):
        answer = http.get(base + "/api/v1/interop/remisiones", headers=doctor)
        rows_back = [r for r in (answer.json() if answer.status_code == 200 else [])
                     if r.get("solicitud_id") == request_id]
        state = rows_back[0]["estado"] if rows_back else ""
        if state == "aceptada":
            break
        sleep(1.0)
    rows.append(Row("El medico ve la respuesta del socio", "aceptada", state or "-", state == "aceptada"))


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    http: httpx.Client | None = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--self", dest="self_test", action="store_true",
                        help="probar contra SaludRed mismo (base por defecto http://localhost:8000)")
    parser.add_argument("--negativos", action="store_true",
                        help="ademas, provocar a proposito 401, 403, 409, 422 y 202")
    parser.add_argument("--sin-auditoria", action="store_true", help="no escribir INTEROP_SENT")
    parser.add_argument("--remision-ida-vuelta", action="store_true",
                        help="R30: Bundle de remision, Bundle invalido, Task desconocida y, si hay "
                             "cuentas del personal en el entorno, el ciclo completo")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env

    base = (env.get("PROBAR_BASE_URL") or (DEFAULT_SELF_URL if args.self_test else "")).rstrip("/")
    missing = [
        name for name in ("PROBAR_CLIENT_ID", "PROBAR_CLIENT_SECRET", "PROBAR_SIGNING_SECRET", "PROBAR_DOC_NUMBER")
        if not env.get(name)
    ]
    if not base:
        missing.insert(0, "PROBAR_BASE_URL")
    if missing:
        out("Faltan variables de entorno: " + ", ".join(missing))
        return 2
    secret, _system = _federated(env)
    if secret is None:
        out("FEDERATED_SECRET no esta configurado: sin el no se puede calcular el identificador "
            "federado del paciente. No se envia nada.")
        return 2

    own_http = http is None
    http = http or httpx.Client(timeout=20)
    sender = InteropSender(
        base, env["PROBAR_CLIENT_ID"], env["PROBAR_CLIENT_SECRET"], env["PROBAR_SIGNING_SECRET"],
        http=http, sleep=sleep,
    )

    def audit(result: cliente.SendResult) -> None:
        if args.sin_auditoria:
            return
        try:
            factory = session_factory
            if factory is None:
                from app.core.database import SessionLocal as factory  # noqa: N813
            with factory() as db:
                cliente.audit_sent(db, result, base_url=base, client_id=env["PROBAR_CLIENT_ID"])
        except Exception as exc:  # noqa: BLE001 -- a missing database must not stop the exchange
            out(f"   (auditoria INTEROP_SENT omitida: {exc.__class__.__name__})")

    rows: list[Row] = []
    out(f"Destino : {base}")
    out(f"Cliente : {env['PROBAR_CLIENT_ID']}" + ("   [modo --self: SaludRed contra SaludRed]" if args.self_test else ""))
    out(f"Paciente: {env.get('PROBAR_DOC_TYPE', 'CC')} (identificador federado, no se envia el documento)")
    out("")
    try:
        run_pair(sender, env, rows, audit)
        if args.negativos:
            run_negatives(http, sender, env, base, rows, sleep)
        if args.remision_ida_vuelta:
            trip_sender = InteropSender(
                base, env["PROBAR_CLIENT_ID"], env["PROBAR_CLIENT_SECRET"], env["PROBAR_SIGNING_SECRET"],
                http=http, sleep=sleep, scopes=(clientes.SCOPE_BUNDLE, clientes.SCOPE_TASK),
            )
            run_round_trip(trip_sender, env, rows, audit)
            run_staff_round_trip(http, base, env, rows, sleep)
    finally:
        if own_http:
            http.close()

    render(rows, out)
    failed = [r for r in rows if not r.ok]
    out("")
    out(f"{len(rows) - len(failed)} de {len(rows)} pasos correctos.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
