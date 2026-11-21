# ruff: noqa: F811
"""``registrar_socio`` and ``probar_sistema`` (the pair tester), plus the migration's shape."""

from __future__ import annotations

import importlib.util
import re
from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models import AuditLog
from app.models.enums import AuditAction
from app.models.interop import InteropClient
from app.services.interop import clientes
from scripts import probar_sistema, registrar_socio
from tests.interop_helpers import FEDERATED_SECRET, register
from tests.test_ai_reports import CANARY, _create_patient  # noqa: F401
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


def _factory(db):  # noqa: ANN001, ANN202
    return lambda: nullcontext(db)


# -- registrar_socio ------------------------------------------------------------------------


def test_registering_prints_the_credentials_once_and_stores_no_clear_secret(db, field_keys) -> None:
    lines: list[str] = []
    code = registrar_socio.main(
        ["--client-id", "equipo7", "--nombre", "Equipo Siete"], session_factory=_factory(db), out=lines.append)
    text = "\n".join(lines)
    assert code == 0 and "client_id" in text and "equipo7" in text
    client_secret = next(line.split(": ", 1)[1] for line in lines if "client_secret" in line)
    signing = next(line.split(": ", 1)[1] for line in lines if "secreto_de_firma" in line)

    row = db.scalar(select(InteropClient).where(InteropClient.client_id == "equipo7"))
    assert row.secret_hash.startswith("$argon2id$") and client_secret not in row.secret_hash
    assert row.signing_secret.startswith("v1.") and signing not in row.signing_secret  # encrypted
    assert clientes.open_signing_secret(row) == signing
    assert row.scopes == list(clientes.KNOWN_SCOPES) and row.is_active


def test_without_encryption_keys_registration_refuses_unless_plaintext_is_accepted(db) -> None:
    lines: list[str] = []
    assert registrar_socio.main(["--client-id", "equipo7"], session_factory=_factory(db), out=lines.append) == 1
    assert "FIELD_ENCRYPTION_KEYS" in lines[0]
    assert db.scalar(select(InteropClient)) is None

    lines.clear()
    code = registrar_socio.main(["--client-id", "equipo7", "--permitir-texto-plano"],
                                session_factory=_factory(db), out=lines.append)
    assert code == 0 and any("texto plano" in line for line in lines)
    assert db.scalar(select(InteropClient)).signing_secret.startswith(clientes.PLAIN_PREFIX)


def test_registration_validates_the_id_the_scopes_and_duplicates(db) -> None:
    common = {"session_factory": _factory(db), "out": lambda _l: None}
    assert registrar_socio.main(["--client-id", "X", "--permitir-texto-plano"], **common) == 1
    assert registrar_socio.main(["--client-id", "equipo7", "--alcances", "system/Patient.write",
                                 "--permitir-texto-plano"], **common) == 1
    assert registrar_socio.main(["--client-id", "equipo7", "--permitir-texto-plano"], **common) == 0
    assert registrar_socio.main(["--client-id", "equipo7", "--permitir-texto-plano"], **common) == 1


def test_listing_shows_clients_without_secrets(db) -> None:
    register(db, "equipo7")
    lines: list[str] = []
    assert registrar_socio.main(["--listar"], session_factory=_factory(db), out=lines.append) == 0
    assert lines and lines[0].startswith("equipo7") and "system/Observation.write" in lines[0]
    assert "argon2" not in lines[0]


# -- probar_sistema ----------------------------------------------------------------------------


@pytest.fixture
def tester(db, client_as, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(get_settings(), "federated_secret", FEDERATED_SECRET)
    _create_patient(db, client_as, with_vitals=False)
    full = register(db, "equipo1")
    narrow = register(db, "equipo2", ("system/Observation.write",))
    env = {
        "PROBAR_BASE_URL": "http://testserver",
        "PROBAR_CLIENT_ID": full.client_id,
        "PROBAR_CLIENT_SECRET": full.client_secret,
        "PROBAR_SIGNING_SECRET": full.signing_secret,
        "PROBAR_DOC_TYPE": CANARY["document_type"],
        "PROBAR_DOC_NUMBER": CANARY["document_number"],
        "PROBAR_ORG_CODE": "IPS-NORTE",
        "PROBAR2_CLIENT_ID": narrow.client_id,
        "PROBAR2_CLIENT_SECRET": narrow.client_secret,
        "PROBAR2_SIGNING_SECRET": narrow.signing_secret,
        "FEDERATED_SECRET": FEDERATED_SECRET,
    }
    return db, env, TestClient(app), [full, narrow]


def _run(tester, *flags):  # noqa: ANN001, ANN002, ANN202
    db, env, http, creds = tester
    lines: list[str] = []
    code = probar_sistema.main(list(flags), env=env, http=http, out=lines.append,
                               sleep=lambda _s: None, session_factory=_factory(db))
    return code, lines, creds


def test_the_pair_tester_sends_the_three_messages_and_prints_a_table(tester) -> None:
    code, lines, creds = _run(tester)
    text = "\n".join(lines)
    assert code == 0, text
    for step in ("Token OAuth2", "Glucosa 182 mg/dL (normal)", "Glucosa 310 mg/dL (critica)", "Remision"):
        assert step in text
    assert "4 de 4 pasos correctos." in text
    assert text.count("OK") == 4 and "FALLA" not in text
    assert "201" in text


def test_the_tester_never_prints_a_secret(tester) -> None:
    _code, lines, creds = _run(tester, "--negativos")
    text = "\n".join(lines)
    for credential in creds:
        assert credential.client_secret not in text and credential.signing_secret not in text
    assert FEDERATED_SECRET not in text
    assert tester[1]["PROBAR_DOC_NUMBER"] not in text  # the document is not printed either


def test_the_negatives_produce_401_403_409_422_and_202_on_purpose(tester) -> None:
    code, lines, _creds = _run(tester, "--negativos")
    text = "\n".join(lines)
    assert code == 0, text
    assert "FALLA" not in text
    expected = {
        "Sin token": "401",
        "Cuerpo alterado tras firmar": "401",
        "Mensaje firmado hace una hora": "401",
        "Mismo mensaje reenviado (nonce repetido)": "409",
        "Sistema LOINC mal escrito": "422",
        "Paciente desconocido (cola de conciliacion)": "202",
        "Alcance faltante (cliente solo Observation)": "403",
    }
    table = {}
    for line in lines:
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) == 5 and cells[0].isdigit():
            table[cells[1]] = cells[3]
    for step, status in expected.items():
        assert table.get(step) == status, (step, table)


def test_the_tester_audits_every_delivery_as_interop_sent(tester) -> None:
    db = tester[0]
    _run(tester)
    db.expire_all()
    sent = select(AuditLog).where(AuditLog.action == AuditAction.INTEROP_SENT).order_by(AuditLog.seq)
    rows = list(db.scalars(sent))
    assert [r.metadata_json["recurso"] for r in rows] == ["Observation", "Observation", "ServiceRequest"]
    assert all(r.metadata_json["codigo_http"] in (201, 202) for r in rows)
    _run(tester, "--sin-auditoria")
    db.expire_all()
    assert len(list(db.scalars(sent))) == 3


def test_a_wrong_secret_is_reported_in_the_table_and_the_exit_code_is_nonzero(tester) -> None:
    db, env, http, _creds = tester
    env["PROBAR_CLIENT_SECRET"] = "wrong"
    lines: list[str] = []
    code = probar_sistema.main([], env=env, http=http, out=lines.append, sleep=lambda _s: None,
                               session_factory=_factory(db))
    assert code == 1 and any("FALLA" in line for line in lines)
    assert any("invalid_client" in line for line in lines)


def test_missing_variables_and_a_missing_federated_secret_are_reported_before_sending(tester, monkeypatch) -> None:
    db, env, http, _creds = tester
    lines: list[str] = []
    assert probar_sistema.main([], env={}, http=http, out=lines.append) == 2
    assert "PROBAR_BASE_URL" in lines[0] and "PROBAR_CLIENT_SECRET" in lines[0]

    monkeypatch.setattr(get_settings(), "federated_secret", None)
    env.pop("FEDERATED_SECRET")
    lines.clear()
    assert probar_sistema.main([], env=env, http=http, out=lines.append) == 2
    assert "FEDERATED_SECRET" in lines[0]


def test_self_mode_defaults_to_the_local_url(tester) -> None:
    _db, env, http, _creds = tester
    env.pop("PROBAR_BASE_URL")
    lines: list[str] = []
    probar_sistema.main(["--self", "--sin-auditoria"], env=env, http=http, out=lines.append,
                        sleep=lambda _s: None)
    assert lines[0] == "Destino : http://localhost:8000"
    assert "modo --self" in lines[1]


# -- migration ---------------------------------------------------------------------------------------


def _load(revision: str):  # noqa: ANN202
    path = next(Path("migrations/versions").glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.source = path.read_text()
    return module


def test_the_interop_migration_chains_after_the_audit_chain_and_is_additive() -> None:
    module = _load("b7e5d9a3c418")
    assert module.down_revision == "f1d6c4a8b250" and module.revision == "b7e5d9a3c418"
    up = module.source.split("def upgrade()")[1].split("def downgrade()")[0]
    for forbidden in ("UPDATE ", "DELETE FROM", "drop_", "DROP "):
        assert forbidden not in up
    for added in ('"interop_clients"', '"interop_nonces"', '"id_federado"', '"origen_externo"',
                  '"estado_conciliacion"', '"referencia_externa"'):
        assert added in up
    # The only relaxation: a partner's referral has no local sender.
    assert up.count("alter_column") == 1 and "nullable=True" in up.split("alter_column")[1]


def test_the_downgrade_stops_when_external_referrals_exist() -> None:
    module = _load("b7e5d9a3c418")
    down = module.source.split("def downgrade()")[1]
    assert "referring_user_id IS NULL" in down and "raise RuntimeError" in down
    assert down.index("raise RuntimeError") < down.index("nullable=False")
    for dropped in ("interop_clients", "interop_nonces", "id_federado"):
        assert dropped in down


def test_the_audit_actions_the_exchange_needs_already_exist() -> None:
    module = _load("a2f7c4d19b60")
    needed = {"INTEROP_RECEIVED", "INTEROP_REJECTED", "INTEROP_SENT", "PATIENT_RECONCILED"}
    assert needed <= set(module.AUDIT_ACTIONS)
