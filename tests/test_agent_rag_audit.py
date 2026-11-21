# ruff: noqa: F811
"""Final delivery, batch A: cited sources (R23), audited payload and roles (R24), no
personal data towards the LLM including the address (R12).

Every model here is fake (tests/agent_helpers.py, tests/rag_helpers.py): nothing reaches
Groq or any embedding service.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from app.core import authz
from app.models import Patient
from app.models.enums import AuditAction, RoleCode
from app.services import ml_client, pseudonymization
from app.services.agent import flow, guidelines, tools as agent_tools
from app.services.agent.context import AgentContext
from app.services.agent.harness import AUDIT_RESULT_CHARS, cited_sources
from app.services.agent.presentation import NO_SUPPORT_TEXT
from app.services.rag import llm as rag_llm
from app.services.rag import privacy
from tests import agent_helpers
from tests.agent_helpers import ScriptedModel
from tests.rag_helpers import FakeEmbedMl, FakeLlm, load_tiny_corpus
from tests.reactive_helpers import add_specialist, audit_rows
from tests.test_ai_reports import CANARY, _create_patient
from tests.test_bed_rules import _user, client_as, db  # noqa: F401  (fixture reuse)

CHAT = "/api/v1/agente/chat"
DECIDE = "/api/v1/agente/decidir"
START = "/api/v1/agente/inicio"
RAG_ASK = "/api/v1/rag/ask"
TRIAGE_QUESTION = "Segun la norma de triage, atencion inmediata riesgo vital?"

# Every value that identifies the canary patient (tests/test_ai_reports.py::CANARY).
FORBIDDEN = (
    "Zacarias",
    "Quimbayaxx",
    "7788990011",
    "3109998877",
    "canary.zacarias",
    "canario",
    "Canarilandia",
    "Carrera 77",
    "88-99",
)


@pytest.fixture
def scene(db, client_as):
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    rag_llm.set_client(FakeLlm("Se usa glucagón o dextrosa intravenosa [2]."))
    patient = _create_patient(db, client_as)
    add_specialist(db, username="especialista.sur", org_code="IPS-SUR")
    model = ScriptedModel()
    agent_helpers.install(db, model)
    yield db, client_as, patient, model
    agent_helpers.uninstall()


def _chat(client, mensaje: str, **body):  # noqa: ANN001, ANN202
    return client.post(CHAT, json={"mensaje": mensaje, **body})


# ---------------------------------------------------------------------------
# R23: the answer cites the document and the fragment used
# ---------------------------------------------------------------------------


def test_a_supported_question_returns_the_document_and_the_fragment_used(scene) -> None:
    db, client_as, _patient, model = scene
    response = _chat(client_as("operador.norte"), TRIAGE_QUESTION)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["estado"] == "respondido" and body["respuesta"]
    assert body["fuentes"], "a supported answer must carry sources"
    for source in body["fuentes"]:
        assert source["documento"] and source["titulo"] and source["fragmento"]
        assert source["chunk_id"].startswith(source["documento"])
        # The citation is the evidence the model was given, not a later lookup.
        assert source["fragmento"][:40] in model.seen_text
        assert f"[{source['chunk_id']}]" in model.seen_text
    assert body["fuentes"][0]["documento"] == "T01"
    assert [s["n"] for s in body["fuentes"]] == list(range(1, len(body["fuentes"]) + 1))
    # The same passages the audit recorded as consulted.
    row = [r for r in audit_rows(db, AuditAction.AGENT_TOOL) if r.metadata_json["tool"] == "buscar_guias"][-1]
    assert {s["chunk_id"] for s in body["fuentes"]} == set(row.metadata_json["chunk_ids"])


def test_without_support_the_agent_says_so_and_does_not_invent(scene, monkeypatch) -> None:
    db, client_as, _patient, _model = scene
    monkeypatch.setattr(
        guidelines,
        "search",
        lambda _db, _user, _patient, _question: guidelines.GuidelineSearch(
            False, "SIN_EVIDENCIA: no hay fragmentos suficientes en las guías para esa pregunta."
        ),
    )
    response = _chat(client_as("operador.norte"), "Segun la guia de triage, cual es la dosis de un farmaco inventado?")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["estado"] == "respondido"
    assert body["respuesta"] == NO_SUPPORT_TEXT and "no voy a inventar" in body["respuesta"]
    assert body["fuentes"] == []  # empty ONLY because there was no support
    assert any(row.metadata_json["tool"] == "buscar_guias"
               for row in audit_rows(db, AuditAction.AGENT_TOOL))


def test_a_fixed_reply_still_has_the_fuentes_field(scene) -> None:
    _db, client_as, _patient, model = scene
    body = _chat(client_as("operador.norte"), "como cambio mi contrasena de acceso?").json()
    assert body["estado"] == "respondido" and body["fuentes"] == []
    assert model.calls == []  # answered by the router, no model call


def test_the_degraded_answer_also_cites_its_sources(scene) -> None:
    _db, client_as, _patient, model = scene

    class RateLimitError(Exception):
        pass

    model.error = RateLimitError("429")
    body = _chat(client_as("operador.norte"), TRIAGE_QUESTION).json()
    assert body["estado"] == "degradado" and body["fuentes"]
    assert {"documento", "fragmento", "chunk_id", "titulo", "n"} <= set(body["fuentes"][0])


def test_the_rag_answer_exposes_respuesta_and_fuentes_next_to_the_english_names(scene) -> None:
    _db, client_as, _patient, _model = scene
    response = client_as("operador.norte").post(
        RAG_ASK, json={"question": "¿Qué hago con un paciente con hipoglucemia que está inconsciente?"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["respuesta"] == body["answer"] and body["fuentes"] == body["sources"]
    assert body["fuentes"] and body["fuentes"][0]["doc_id"] == "G01"
    assert all(s["fragmento"] for s in body["fuentes"])  # the fragment, not only the id
    for source in body["fuentes"]:
        assert source["documento"] == source["doc_id"]
        assert source["titulo"] == source["title"]


def test_an_abstention_has_empty_fuentes(scene) -> None:
    _db, client_as, _patient, _model = scene
    body = client_as("operador.norte").post(RAG_ASK, json={"question": "¿Quién ganó el mundial de fútbol de 2014?"}).json()
    assert body["mode"] == "abstencion" and body["fuentes"] == [] and body["respuesta"] == body["answer"]


# ---------------------------------------------------------------------------
# R24: every tool call is audited with the pseudonymized payload sent to the LLM
# ---------------------------------------------------------------------------


def test_every_tool_call_is_audited_with_a_payload_that_holds_no_canary(scene) -> None:
    db, client_as, patient, model = scene
    client = client_as("operador.norte")
    question = (
        "Segun la guia de triage, que criterio aplica a este paciente? Zacarias Quimbayaxx CC 7788990011, "
        "telefono 3109998877, correo canary.zacarias@canario.test, vive en Carrera 77 numero 88-99 Canarilandia"
    )
    assert _chat(client, question, paciente_id=patient["id"]).status_code == 200
    pending = _chat(
        client, "Remitir a este paciente con especialista.sur por dificultad respiratoria",
        paciente_id=patient["id"], conversacion_id="rem",
    )
    assert pending.json()["estado"] == "pendiente_aprobacion"
    done = client.post(DECIDE, json={"conversacion_id": "rem", "decision": "approve"})
    assert done.status_code == 200, done.text
    assert _chat(client, "cargar skill triage-criticidad", conversacion_id="skill").status_code == 200
    preference = _chat(client, "recuerda que prefiero respuestas breves", conversacion_id="preference")
    assert preference.json()["estado"] == "pendiente_aprobacion"
    assert client.post(DECIDE, json={"conversacion_id": "preference", "decision": "approve"}).status_code == 200

    rows = audit_rows(db, AuditAction.AGENT_TOOL)
    tools = {r.metadata_json["tool"] for r in rows}
    assert {"consultar_paciente", "buscar_guias", "listar_especialistas", "proponer_remision",
            "cargar_skill", "guardar_preferencia"} <= tools
    for row in rows:
        meta = row.metadata_json
        payload = meta["payload_llm"]
        assert isinstance(payload["enviado_al_llm"], str) and payload["enviado_al_llm"], meta["tool"]
        assert isinstance(payload["truncado"], bool)
        assert len(json.dumps(meta)) < 8000  # bounded
        dumped = json.dumps(meta, default=str)
        for value in FORBIDDEN:
            assert value not in dumped, (meta["tool"], value)
        # The row names the patient (the audit's own link); the payload sent out never does.
        assert patient["id"] not in json.dumps(payload, default=str), meta["tool"]

    summary = next(r for r in rows if r.metadata_json["tool"] == "consultar_paciente").metadata_json
    alias = pseudonymization.pseudonym_for(patient["id"])
    assert alias in summary["payload_llm"]["enviado_al_llm"]
    # What was stored is what the model was shown (same pseudonymized text).
    assert summary["payload_llm"]["enviado_al_llm"][:120] in model.seen_text
    # And the model itself never saw a canary value either.
    for value in (*FORBIDDEN, patient["id"]):
        assert value not in model.seen_text, value
    for row in audit_rows(db, AuditAction.AGENT_CHAT):
        payload = row.metadata_json["payload_llm"]
        assert len(payload["enviado_al_llm"]) <= AUDIT_RESULT_CHARS
        assert payload["enviado_al_llm"] in model.seen_text
        for value in (*FORBIDDEN, patient["id"]):
            assert value not in json.dumps(payload), value


def test_the_audited_payload_is_cut_to_a_fixed_size(scene, monkeypatch) -> None:
    db, client_as, _patient, _model = scene
    monkeypatch.setattr(
        guidelines,
        "search",
        lambda _db, _user, _patient, _question: guidelines.GuidelineSearch(True, "A" * 20_000, ["X-1"]),
    )
    assert _chat(client_as("operador.norte"), "Segun la guia de triage, que criterio aplica?").status_code == 200
    row = [r for r in audit_rows(db, AuditAction.AGENT_TOOL) if r.metadata_json["tool"] == "buscar_guias"][-1]
    payload = row.metadata_json["payload_llm"]
    assert len(payload["enviado_al_llm"]) == AUDIT_RESULT_CHARS and payload["truncado"] is True


def test_the_audit_and_model_receive_the_same_sanitized_tool_result(scene, monkeypatch) -> None:
    """A contaminated tool result must be cleaned before the model or audit sees it."""

    db, client_as, patient, model = scene
    leaky = "Criterio X. Paciente Zacarias Quimbayaxx, CC 7788990011, vive en Carrera 77 numero 88-99 Canarilandia."
    monkeypatch.setattr(
        guidelines,
        "search",
        lambda _db, _user, _patient, _question: guidelines.GuidelineSearch(True, leaky, ["X-1"]),
    )
    _chat(client_as("operador.norte"), "Segun la guia de triage, que criterio aplica?", paciente_id=patient["id"])
    row = [r for r in audit_rows(db, AuditAction.AGENT_TOOL) if r.metadata_json["tool"] == "buscar_guias"][-1]
    dumped = json.dumps(row.metadata_json, default=str)
    for value in FORBIDDEN:
        assert value not in dumped, value
        assert value not in model.seen_text, value
    assert "Criterio X" in dumped
    assert row.metadata_json["payload_llm"]["enviado_al_llm"] in model.seen_text


class _UncitedModel(ScriptedModel):
    """Answers with fixed prose and never calls a tool, whatever it is asked."""

    prose: str = ""

    def _answer(self, messages):
        return AIMessage(content=self.prose)


@pytest.mark.parametrize("greeting", ["hola", "Hola!", "Buenos días", "gracias", "¡Gracias!"])
def test_a_greeting_without_retrieval_is_preserved(scene, greeting) -> None:
    # Behaviour changed on purpose (R23). This test used to send a CLINICAL question to a
    # model that answered without any tool and asserted the prose survived; that is exactly the
    # hole (clinical prose with no citation). Only a bare greeting or courtesy may answer
    # without evidence now.
    db, client_as, _patient, _model = scene
    answer = "Hola, ¿en qué puedo ayudarle?"
    agent_helpers.install(db, _UncitedModel(prose=answer))
    response = _chat(client_as("operador.norte"), greeting)
    assert response.status_code == 200
    assert response.json()["respuesta"] == answer
    assert response.json()["fuentes"] == []


@pytest.mark.parametrize(
    "question",
    [TRIAGE_QUESTION, "¿Qué dosis de metformina se usa en un adulto?", "hola, necesito la guia de triage"],
)
def test_clinical_prose_without_calling_an_evidence_tool_abstains(scene, question) -> None:
    db, client_as, _patient, _model = scene
    invented = "Se usa 850 mg cada 12 horas y no hace falta consultar nada más."
    agent_helpers.install(db, _UncitedModel(prose=invented))
    body = _chat(client_as("operador.norte"), question).json()
    assert body["estado"] == "respondido"
    assert body["respuesta"] == NO_SUPPORT_TEXT and invented not in body["respuesta"]
    assert body["fuentes"] == []


def test_loading_a_skill_after_an_empty_search_still_abstains(scene, monkeypatch) -> None:
    """``cargar_skill`` output is a procedure, not support: it must not cancel the abstention."""

    db, client_as, _patient, _model = scene
    monkeypatch.setattr(
        guidelines, "search",
        lambda _db, _user, _patient, _question: guidelines.GuidelineSearch(False, "SIN_EVIDENCIA: nada."),
    )
    body = _chat(client_as("operador.norte"), "cargar skill triage-criticidad segun la guia de triage").json()
    assert body["estado"] == "respondido"
    assert body["respuesta"] == NO_SUPPORT_TEXT
    assert body["fuentes"] == []
    rows = audit_rows(db, AuditAction.AGENT_TOOL)
    assert {"cargar_skill", "buscar_guias"} <= {r.metadata_json["tool"] for r in rows}


def test_a_skill_alone_is_answered_with_the_skill_text_not_with_invented_prose(scene) -> None:
    _db, client_as, _patient, _model = scene
    body = _chat(client_as("operador.norte"), "cargar skill triage-criticidad").json()
    assert "Interpretación de criticidad" in body["respuesta"] and body["fuentes"] == []


def _tool_message(name: str, artifact: dict | None, content: str = "texto") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=uuid.uuid4().hex, artifact=artifact)


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param([_tool_message("cargar_skill", None, "1. Llame a la herramienta")], id="skill-only"),
        pytest.param([_tool_message("listar_especialistas", None, "- id · Dr. X")], id="specialists-only"),
        pytest.param([_tool_message("buscar_guias", {"found": False, "sources": []}, "SIN_EVIDENCIA"),
                      _tool_message("cargar_skill", None, "1. Llame a la herramienta"),
                      _tool_message("listar_especialistas", None, "- id · Dr. X")], id="empty-search-plus-others"),
        pytest.param([_tool_message("consultar_paciente", {}, "SIN_PACIENTE: no hay paciente")], id="no-patient"),
        pytest.param([_tool_message("buscar_guias", {}, "NO_AUTORIZADO: no")], id="search-refused"),
    ],
)
def test_only_the_two_evidence_tools_count_as_support(tools) -> None:
    sources, unsupported = cited_sources([HumanMessage(content="pregunta clinica"), *tools])
    assert unsupported is True and sources == []


def test_a_patient_record_or_a_non_empty_search_is_support() -> None:
    source = {"chunk_id": "T01-1", "documento": "T01", "fragmento": "x"}
    record = {"chunk_id": "CTX-pz-1", "documento": "historia", "fragmento": "y"}
    for tool in (
        _tool_message("buscar_guias", {"found": True, "sources": [source]}),
        _tool_message("consultar_paciente", {"found": True, "sources": [record]}),
    ):
        sources, unsupported = cited_sources([HumanMessage(content="pregunta"), tool])
        assert unsupported is False and len(sources) == 1
    # Evidence from a PREVIOUS turn does not support this one.
    old = _tool_message("buscar_guias", {"found": True, "sources": [source]})
    sources, unsupported = cited_sources([HumanMessage(content="a"), old, AIMessage(content="r"),
                                          HumanMessage(content="otra pregunta")])
    assert unsupported is True and sources == []


def test_empty_retrieval_does_not_overwrite_patient_data(scene, monkeypatch):
    _db, client_as, patient, _model = scene
    monkeypatch.setattr(guidelines, "search", lambda *args: guidelines.GuidelineSearch(False, "SIN_EVIDENCIA"))
    body = _chat(client_as("operador.norte"), "Datos del paciente segun la guia",
                 paciente_id=patient["id"]).json()
    assert body["respuesta"] != NO_SUPPORT_TEXT
    assert body["fuentes"][0]["documento"] == "historia"


def test_buscar_guias_itself_refuses_a_patient_from_another_ips(scene):
    db, _client_as, patient, _model = scene
    user = _user(db, "especialista.sur")
    ctx = AgentContext(user.id, user.username, "especialista", uuid.UUID(patient["id"]),
                       pseudonymization.pseudonym_for(patient["id"]))
    content, artifact = agent_tools.buscar_guias.func(TRIAGE_QUESTION, SimpleNamespace(context=ctx))
    assert content.startswith("NO_AUTORIZADO")
    assert artifact == {}


@pytest.mark.parametrize("phone", ["3109998877", "310 999 8877", "310-999-8877",
                                  "+57 310 999 8877", "57-310-999-8877"])
def test_generic_scrub_removes_spaced_and_prefixed_mobile_numbers(phone):
    assert privacy.scrub_generic(f"Telefono {phone}") == "Telefono [TELEFONO]"


@pytest.mark.parametrize("document", ["123456", "7788990011"])
def test_generic_scrub_removes_bare_document_numbers(document):
    assert privacy.scrub_generic(f"Consultar {document} por favor") == "Consultar [DOCUMENTO] por favor"


@pytest.mark.parametrize("clinical", ["glucosa 180", "FC 110", "plaquetas 150000",
                                     "150000 UI", "carga viral 123456 copias/ml",
                                     "0.123456", "123456.78", "pz-12345678"])
def test_generic_scrub_preserves_clinical_numbers(clinical):
    assert privacy.scrub_generic(clinical) == clinical


@pytest.mark.parametrize("document", ["123456", "7788990011", "5550001112"])
def test_patient_bound_scrub_removes_bare_document_numbers_that_are_not_the_patients(document) -> None:
    # The stored document is 7788990011; a relative's number typed without "CC" must go too.
    out = privacy.scrub(f"Consultar {document} por favor", _identity())
    assert "[DOCUMENTO]" in out and document not in out


@pytest.mark.parametrize("clinical", ["glucosa 180", "FC 110", "plaquetas 150000",
                                     "150000 UI", "carga viral 123456 copias/ml",
                                     "0.123456", "123456.78", "pz-12345678"])
def test_patient_bound_scrub_preserves_clinical_numbers_like_the_generic_one(clinical) -> None:
    assert privacy.scrub(clinical, _identity()) == clinical == privacy.scrub_generic(clinical)


@pytest.mark.parametrize("text", ["hola", "Hola!", "¡Gracias!", "buenos dias", "ayuda"])
def test_only_bare_greetings_and_courtesies_are_pleasantries(text) -> None:
    assert flow.is_pleasantry(text)


@pytest.mark.parametrize("text", ["hola, consulta de triage", "dosis de metformina", "gracias por la guia de triage",
                                  "¿Qué opinas de esto?", ""])
def test_anything_else_needs_evidence(text) -> None:
    assert not flow.is_pleasantry(text)


def test_multiple_searches_have_stable_citations_and_unique_display_numbers() -> None:
    sources = [
        {"n": 1, "chunk_id": cid, "documento": cid.split("-")[0], "fragmento": cid}
        for cid in ("T01-1", "G01-1")
    ]
    messages = [HumanMessage(content="criterios")]
    for index, source in enumerate((*sources, sources[0])):
        messages.append(ToolMessage(
            content=f"[{source['chunk_id']}] {source['fragmento']}", name="buscar_guias",
            tool_call_id=str(index), artifact={"found": True, "sources": [source]},
        ))
    actual, no_support = cited_sources(messages)
    assert not no_support
    assert [s["n"] for s in actual] == [1, 2]
    assert [s["chunk_id"] for s in actual] == ["T01-1", "G01-1"]


@pytest.mark.parametrize("marker", ["numero", "número", "No.", "#"])
def test_a_partial_street_address_is_removed_with_or_without_a_patient(marker: str) -> None:
    typed = f"Vive en Carrera 77 {marker} 88-99 con fiebre"
    for result in (privacy.scrub_generic(typed), privacy.scrub(typed, _identity())):
        assert "77" not in result and "88-99" not in result
        assert "[DIRECCION]" in result and "fiebre" in result


AGENT_CALLS = [
    ("post", CHAT, {"mensaje": "hola, consulta de triage"}),
    ("post", START, {}),
    ("post", DECIDE, {"conversacion_id": "x", "decision": "approve"}),
]


def test_the_accountant_role_is_not_a_clinical_or_staff_role() -> None:
    assert RoleCode.ACCOUNTANT not in authz.AI_REVIEWERS
    assert RoleCode.ACCOUNTANT not in authz.STAFF_ROLES


def test_an_accountant_gets_403_on_every_agent_and_rag_endpoint(scene) -> None:
    _db, client_as, patient, _model = scene
    suggestion = uuid.uuid4()
    rag_calls = [
        ("post", RAG_ASK, {"question": "hipoglucemia inconsciente"}),
        ("post", RAG_ASK, {"question": "hipoglucemia inconsciente", "patient_id": patient["id"]}),
        ("get", "/api/v1/rag/suggestions", None),
        ("get", f"/api/v1/rag/suggestions/{suggestion}", None),
        ("post", f"/api/v1/rag/suggestions/{suggestion}/approve", {}),
        ("post", f"/api/v1/rag/suggestions/{suggestion}/reject", {"motivo": "no corresponde"}),
        ("get", "/api/v1/rag/stats", None),
    ]
    client = client_as("contable.eps")
    for method, path, body in (*AGENT_CALLS, *rag_calls):
        response = client.post(path, json=body) if method == "post" else client.get(path)
        assert response.status_code == 403, (method, path, response.status_code, response.text)
    # The same calls are not blocked for the sake of it: a clinician is served.
    assert _chat(client_as("operador.norte"), TRIAGE_QUESTION).status_code == 200
    assert client_as("operador.norte").get("/api/v1/rag/stats").status_code == 200


# ---------------------------------------------------------------------------
# R12 extension: name, document, e-mail, phone AND address never reach the LLM
# ---------------------------------------------------------------------------


def _identity(address: str | None = CANARY["address"]) -> privacy.PatientIdentity:
    return privacy.PatientIdentity(
        pseudonym="pz-0123456789abcdef",
        first_name=CANARY["first_name"],
        last_name=CANARY["last_name"],
        document_number=CANARY["document_number"],
        phone=CANARY["phone"],
        email=CANARY["email"],
        address=address,
    )


def test_the_address_is_scrubbed_however_it_was_typed() -> None:
    for typed in (
        "vive en Carrera 77 numero 88-99 Canarilandia",
        "vive en carrera 77, numero 88 - 99 (Canarilandia)",
        "VIVE EN CARRERA   77 NUMERO 88-99\nCANARILANDIA.",
    ):
        out = privacy.scrub(typed, _identity())
        assert "[DIRECCION]" in out, typed
        for value in ("77", "88", "99", "anarilandia", "ANARILANDIA"):
            assert value not in out, (typed, value)


def test_a_street_alone_is_scrubbed_when_the_stored_address_has_several_parts() -> None:
    identity = _identity("Calle 10 # 5-20, Barrio Centro, Cali")
    out = privacy.scrub("Lo recogieron en la calle 10 #5 - 20 y trabaja en Cali", identity)
    assert "[DIRECCION]" in out and "5 - 20" not in out and "calle 10" not in out.lower()
    assert "Cali" in out  # a lone city is not a home address
    assert "[DIRECCION]" in privacy.scrub("vive en barrio centro", identity)


def test_a_street_address_is_scrubbed_even_without_an_open_patient() -> None:
    out = privacy.scrub_generic("Reside en la Calle 45 # 12-34 sur y llamó al 3101234567")
    assert "[DIRECCION]" in out and "12-34" not in out and "[TELEFONO]" in out
    assert "[DIRECCION]" in privacy.scrub_generic("direccion: cra 7 no 45 - 12")


@pytest.mark.parametrize(
    "clinical",
    ["Cr 1.2 mg/dL con bloqueo AV 2 grado", "Calle de triage 3: atencion inmediata", "dosis de 5 mg cada 8 horas"],
)
def test_clinical_text_is_not_mistaken_for_an_address(clinical: str) -> None:
    assert privacy.scrub_generic(clinical) == clinical
    assert privacy.scrub(clinical, _identity()) == clinical


def test_the_agent_message_with_an_address_never_reaches_the_model(scene) -> None:
    _db, client_as, patient, model = scene
    client = client_as("operador.norte")
    typed = "Segun la guia de triage, el paciente vive en la carrera 77, numero 88 - 99 (Canarilandia)"
    assert _chat(client, typed, paciente_id=patient["id"]).status_code == 200
    # With no patient open there is no stored address to look up: the pattern still holds.
    other = _chat(client, "Segun la guia de triage, una persona de Calle 45 # 12-34 con fiebre", conversacion_id="b")
    assert other.status_code == 200
    for value in ("Canarilandia", "Carrera 77", "carrera 77", "88 - 99", "12-34", "Calle 45"):
        assert value not in model.seen_text, value
    assert "[DIRECCION]" in model.seen_text


def test_the_rag_prompt_never_holds_name_document_email_phone_or_address(scene) -> None:
    db, client_as, patient, _model = scene
    llm = FakeLlm("Se usa glucagón o dextrosa intravenosa [2].")
    rag_llm.set_client(llm)
    question = (
        "¿Qué hago con Zacarias Quimbayaxx CC 7788990011 con hipoglucemia inconsciente? Tel 3109998877, "
        "canary.zacarias@canario.test, vive en carrera 77 numero 88-99 Canarilandia"
    )
    response = client_as("operador.norte").post(RAG_ASK, json={"question": question, "patient_id": patient["id"]})
    assert response.status_code == 200, response.text
    everything = json.dumps([response.json(), llm.calls], default=str)
    for value in (*FORBIDDEN, patient["id"]):
        assert value not in everything, value


def test_the_ai_payload_is_an_allowlist_without_any_identifier(scene) -> None:
    db, _client_as, patient, _model = scene
    for banned in ("name", "document", "email", "phone", "address"):
        assert not any(banned in field for field in pseudonymization.PAYLOAD_FIELDS), banned
    row = db.scalar(select(Patient).where(Patient.id == uuid.UUID(patient["id"])))
    payload = pseudonymization.build_criticality_payload(db, row)
    assert set(payload) <= set(pseudonymization.PAYLOAD_FIELDS)
    dumped = json.dumps(payload, default=str)
    for value in (*FORBIDDEN, patient["id"]):
        assert value not in dumped, value
