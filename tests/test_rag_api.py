# ruff: noqa: F811
"""Clinical RAG over HTTP: ask, abstention, privacy canary, sign-off and feedback."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, RagChunk, RagSuggestion
from app.models.enums import AuditAction, RagBase
from app.services import ml_client, pseudonymization
from app.services.rag import llm as rag_llm
from app.services.rag.prompt import ABSTENTION_TEXT
from tests.rag_helpers import BrokenLlm, FakeEmbedMl, FakeLlm, load_tiny_corpus
from tests.test_ai_reports import CANARY, _create_patient
from tests.test_bed_rules import _user, client_as, db  # noqa: F401  (fixture reuse)

QUESTION = "¿Qué hago con un paciente con hipoglucemia que está inconsciente?"


@pytest.fixture
def scene(db, client_as):
    fake_ml = FakeEmbedMl()
    ml_client.set_client(fake_ml)
    load_tiny_corpus(db)
    llm = FakeLlm("Se usa glucagón o dextrosa intravenosa [2].")
    rag_llm.set_client(llm)
    patient = _create_patient(db, client_as)
    return db, client_as, patient, llm


def _ask(client, **body):
    return client.post("/api/v1/rag/ask", json={"question": QUESTION, **body})


def test_a_question_without_patient_answers_with_numbered_sources(scene) -> None:
    _db, client_as, _patient, llm = scene
    response = _ask(client_as("operador.norte"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "llm" and body["answer"].startswith("Se usa glucagón")
    assert body["suggestion_id"] is None and body["retrieval_degraded"] is False
    # Only real evidence is cited: between one and three chunks, numbered from 1.
    assert [s["n"] for s in body["sources"]] == list(range(1, len(body["sources"]) + 1))
    assert 1 <= len(body["sources"]) <= 3
    assert body["sources"][0]["doc_id"] == "G01"
    assert {"chunk_id", "doc_id", "title", "url", "score"} <= set(body["sources"][0])
    system, user = llm.calls[0]
    assert system["role"] == "system" and "SOLO" in system["content"] and ABSTENTION_TEXT in system["content"]
    assert user["content"].startswith("CONTEXTO:\n[1] (") and f"PREGUNTA: {QUESTION}" in user["content"]


def test_an_off_topic_question_abstains_and_stores_nothing(scene) -> None:
    db, client_as, patient, llm = scene
    response = _ask(client_as("operador.norte"), question="¿Quién ganó el mundial de fútbol de 2014?",
                    patient_id=patient["id"])
    body = response.json()
    assert body["mode"] == "abstencion" and body["answer"] == ABSTENTION_TEXT
    assert body["sources"] == [] and body["suggestion_id"] is None
    assert llm.calls == [] and db.scalars(select(RagSuggestion)).first() is None


def test_the_base_filter_narrows_the_guidelines(scene) -> None:
    _db, client_as, _patient, _llm = scene
    body = _ask(client_as("operador.norte"), question="atención inmediata riesgo vital reanimación",
                base="triage").json()
    assert body["sources"] and {s["doc_id"] for s in body["sources"]} == {"T01"}


def test_extractive_mode_without_a_model_and_when_the_model_fails(scene) -> None:
    _db, client_as, _patient, _llm = scene
    for client in (None, BrokenLlm()):
        rag_llm.set_client(client)
        body = _ask(client_as("operador.norte")).json()
        assert body["mode"] == "extractivo" and "[1]" in body["answer"]


def test_a_model_that_abstains_is_an_abstention(scene) -> None:
    db, client_as, patient, _llm = scene
    rag_llm.set_client(FakeLlm(ABSTENTION_TEXT))
    body = _ask(client_as("operador.norte"), patient_id=patient["id"]).json()
    assert body["mode"] == "abstencion" and body["suggestion_id"] is None and body["sources"] == []
    assert db.scalars(select(RagSuggestion)).first() is None


def test_degraded_mode_when_the_embedding_service_is_down(db, client_as) -> None:
    load_tiny_corpus(db, embed=False)  # conftest default: ml is down
    body = _ask(client_as("operador.norte")).json()
    assert body["retrieval_degraded"] is True
    assert body["mode"] == "lexico" and body["sources"][0]["doc_id"] == "G01"


def test_canary_no_personal_data_reaches_the_prompt_or_the_response(scene) -> None:
    db, client_as, patient, llm = scene
    doctor = client_as("operador.norte")
    # The clinician even types the name and the document in the question.
    question = "¿Qué hago con Zacarias Quimbayaxx CC 7788990011 con hipoglucemia inconsciente? Tel 3109998877"
    response = _ask(doctor, question=question, patient_id=patient["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    row = db.get(RagSuggestion, uuid.UUID(body["suggestion_id"]))

    everything = json.dumps([body, row.prompt_sent, row.question, row.sources, llm.calls], default=str)
    forbidden = [
        *[v for k, v in CANARY.items() if k not in {"document_type", "gender"}],
        patient["id"], "Zacarias", "Quimbayaxx", "canario", "7788990011", "3109998877", "1961",
    ]
    for value in forbidden:
        assert value not in everything, value
    # What the model received is exactly what was stored as evidence.
    assert llm.calls[0] == row.prompt_sent
    pseudonym = pseudonymization.pseudonym_for(patient["id"])
    assert pseudonym in json.dumps(row.prompt_sent)
    assert "[DOCUMENTO]" in row.question and "[TELEFONO]" in row.question
    # The patient summary is a numbered, labelled source with the vitals and their units.
    user_message = row.prompt_sent[1]["content"]
    assert "(historia clínica (seudonimizada))" in user_message
    print(user_message)
    assert "Dificultad respiratoria" in user_message and "Pulse oximetry: 88 % (" in user_message
    assert row.sources[0]["title"] == "Historia clínica (seudonimizada)"


def test_a_patient_question_leaves_a_pending_suggestion_and_an_audit_without_text(scene) -> None:
    db, client_as, patient, _llm = scene
    doctor = client_as("operador.norte")
    body = _ask(doctor, patient_id=patient["id"]).json()
    suggestion_id = body["suggestion_id"]
    assert suggestion_id and body["mode"] == "llm"

    listed = doctor.get(f"/api/v1/rag/suggestions?estado=pendiente&patient_id={patient['id']}").json()
    assert [s["id"] for s in listed] == [suggestion_id] and listed[0]["estado"] == "pendiente"
    detail = doctor.get(f"/api/v1/rag/suggestions/{suggestion_id}").json()
    assert detail["prompt_sent"][0]["role"] == "system"

    entry = db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.RAG_QUERY)).all()[-1]
    meta = entry.metadata_json
    assert meta["chunk_ids"] == [s["chunk_id"] for s in body["sources"]] and meta["mode"] == "llm"
    assert "hipoglucemia" not in json.dumps(meta) and body["answer"] not in json.dumps(meta)


def test_full_flow_approve_enters_the_record_and_feeds_the_next_question(scene) -> None:
    db, client_as, patient, llm = scene
    doctor = client_as("operador.norte")
    first = _ask(doctor, patient_id=patient["id"]).json()["suggestion_id"]
    other = _ask(doctor, patient_id=patient["id"], question="triage inmediato riesgo vital").json()["suggestion_id"]
    record_url = f"/api/v1/patients/{patient['id']}/clinical-record"
    assert doctor.get(record_url).json()["sugerencias_rag"] == []  # pending is not the record

    ok = doctor.post(f"/api/v1/rag/suggestions/{first}/approve", json={"observaciones": "De acuerdo"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["estado"] == "aprobado" and ok.json()["observaciones"] == "De acuerdo"
    assert ok.json()["firmado_por"] == _user(db, "operador.norte").full_name
    no = doctor.post(f"/api/v1/rag/suggestions/{other}/reject", json={"motivo": "No aplica al caso"})
    assert no.status_code == 200 and no.json()["estado"] == "rechazado"
    assert no.json()["motivo_rechazo"] == "No aplica al caso"
    assert doctor.delete(f"/api/v1/rag/suggestions/{other}").status_code == 405

    record = doctor.get(record_url).json()
    assert [s["id"] for s in record["sugerencias_rag"]] == [first]
    assert record["sugerencias_rag"][0]["observaciones"] == "De acuerdo"
    assert "prompt_sent" not in record["sugerencias_rag"][0]
    pdf = doctor.get(f"{record_url}.pdf")
    assert pdf.status_code == 200 and first.encode() in pdf.content and other.encode() not in pdf.content

    # Indexed as "historia" for THIS patient, approved only (the rejected one is not).
    chunks = db.scalars(select(RagChunk).where(RagChunk.base == RagBase.HISTORY)).all()
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.patient_pseudonym == pseudonymization.pseudonym_for(patient["id"])
    assert chunk.estado == "aprobado" and chunk.embedding is not None
    assert "Quimbayaxx" not in chunk.text and "[1]" not in chunk.text and "De acuerdo" in chunk.text

    # Retroalimentación: the next question about the patient cites it; without the patient it does not.
    again = _ask(doctor, patient_id=patient["id"], question="respuesta aprobada glucagón dextrosa").json()
    assert chunk.chunk_id in [s["chunk_id"] for s in again["sources"]]
    anonymous = _ask(doctor, question="respuesta aprobada glucagón dextrosa").json()
    assert chunk.chunk_id not in [s["chunk_id"] for s in anonymous["sources"]]

    actions = {a.action for a in db.scalars(select(AuditLog))}
    assert {AuditAction.RAG_SUGGESTION_APPROVED, AuditAction.RAG_SUGGESTION_REJECTED} <= actions


def test_a_suggestion_is_reviewed_once(scene) -> None:
    _db, client_as, patient, _llm = scene
    doctor = client_as("operador.norte")
    a = _ask(doctor, patient_id=patient["id"]).json()["suggestion_id"]
    b = _ask(doctor, patient_id=patient["id"], question="triage inmediato riesgo vital").json()["suggestion_id"]
    assert doctor.post(f"/api/v1/rag/suggestions/{a}/approve", json={}).status_code == 200
    assert doctor.post(f"/api/v1/rag/suggestions/{a}/approve", json={}).status_code == 409
    assert doctor.post(f"/api/v1/rag/suggestions/{a}/reject", json={"motivo": "tarde"}).status_code == 409
    assert doctor.post(f"/api/v1/rag/suggestions/{b}/reject", json={"motivo": "no aplica"}).status_code == 200
    assert doctor.post(f"/api/v1/rag/suggestions/{b}/approve", json={}).status_code == 409
    assert doctor.post(f"/api/v1/rag/suggestions/{uuid.uuid4()}/approve", json={}).status_code == 404


def test_rejecting_requires_a_reason_and_the_review_note_rule_is_enforced(scene, monkeypatch) -> None:
    _db, client_as, patient, _llm = scene
    doctor = client_as("operador.norte")
    suggestion = _ask(doctor, patient_id=patient["id"]).json()["suggestion_id"]
    assert doctor.post(f"/api/v1/rag/suggestions/{suggestion}/reject", json={}).status_code == 422
    assert doctor.post(f"/api/v1/rag/suggestions/{suggestion}/reject", json={"motivo": " "}).status_code == 422
    monkeypatch.setattr(get_settings(), "require_review_note", True)
    assert doctor.post(f"/api/v1/rag/suggestions/{suggestion}/approve", json={"observaciones": "ok"}).status_code == 422
    assert doctor.post(
        f"/api/v1/rag/suggestions/{suggestion}/approve", json={"observaciones": "Concuerda con la clinica"}
    ).status_code == 200


@pytest.mark.parametrize("username", ["admin", "coordinador.eps", "contable.eps", "paciente.demo"])
def test_non_clinicians_cannot_sign_or_ask_about_a_patient(scene, username) -> None:
    _db, client_as, patient, _llm = scene
    suggestion = _ask(client_as("operador.norte"), patient_id=patient["id"]).json()["suggestion_id"]
    client = client_as(username)
    assert client.post(f"/api/v1/rag/suggestions/{suggestion}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/rag/suggestions/{suggestion}/reject", json={"motivo": "xx yy"}).status_code == 403
    assert _ask(client, patient_id=patient["id"]).status_code == 403
    if username in {"contable.eps", "paciente.demo"}:
        assert _ask(client).status_code == 403
        assert client.get("/api/v1/rag/suggestions").status_code == 403
        assert client.get("/api/v1/rag/stats").status_code == 403
    else:  # staff can read the tray and ask general questions
        assert _ask(client).status_code == 200
        assert client.get(f"/api/v1/rag/suggestions/{suggestion}").status_code == 200


def test_another_ips_cannot_touch_the_patient(scene) -> None:
    _db, client_as, patient, _llm = scene
    suggestion = _ask(client_as("operador.norte"), patient_id=patient["id"]).json()["suggestion_id"]
    outsider = client_as("operador.sur")
    assert _ask(outsider, patient_id=patient["id"]).status_code == 403
    assert outsider.get(f"/api/v1/rag/suggestions/{suggestion}").status_code == 403
    assert outsider.post(f"/api/v1/rag/suggestions/{suggestion}/approve", json={}).status_code == 403
    assert outsider.get("/api/v1/rag/suggestions").json() == []


def test_request_validation(scene) -> None:
    _db, client_as, _patient, _llm = scene
    doctor = client_as("operador.norte")
    assert doctor.post("/api/v1/rag/ask", json={"question": "ab"}).status_code == 422
    assert doctor.post("/api/v1/rag/ask", json={"question": "x" * 501}).status_code == 422
    assert doctor.post("/api/v1/rag/ask", json={"question": QUESTION, "base": "otra"}).status_code == 422
    assert doctor.post("/api/v1/rag/ask", json={"question": QUESTION, "extra": 1}).status_code == 422


def test_stats_report_the_index_and_the_configuration(scene) -> None:
    _db, client_as, _patient, _llm = scene
    stats = client_as("operador.norte").get("/api/v1/rag/stats").json()
    assert stats["chunks"] == {"general": 3, "triage": 2, "historia": 0}
    assert stats["total_chunks"] == stats["embedded_chunks"] == 5
    assert stats["has_llm_key"] is True and stats["embedding_dim"] == 384
    assert stats["min_cosine"] == 0.45 and stats["min_bm25"] == 1.5 and stats["mmr_lambda"] == 0.7


def test_a_suggestion_is_immutable_evidence(scene) -> None:
    db, client_as, patient, _llm = scene
    suggestion_id = _ask(client_as("operador.norte"), patient_id=patient["id"]).json()["suggestion_id"]
    row = db.get(RagSuggestion, uuid.UUID(suggestion_id))
    row.prompt_sent = [{"role": "user", "content": "otro"}]
    with pytest.raises(ValueError, match="inmutable"):
        db.commit()
    db.rollback()


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_a_question_without_patient_is_scrubbed_before_the_embedder_and_the_model(db, client_as) -> None:
    fake_ml = FakeEmbedMl()
    ml_client.set_client(fake_ml)
    load_tiny_corpus(db)
    fake_ml.calls.clear()
    llm = FakeLlm()
    rag_llm.set_client(llm)
    question = "hipoglucemia inconsciente CC 1020304050 llamar 3151234567 o a juan.perez@correo.test"
    assert _ask(client_as("operador.norte"), question=question).status_code == 200
    seen = json.dumps([fake_ml.calls, llm.calls], ensure_ascii=False)
    for secret in ("1020304050", "3151234567", "juan.perez", "correo.test"):
        assert secret not in seen, secret
    assert "[DOCUMENTO]" in seen and "[TELEFONO]" in seen and "[CORREO]" in seen


def test_an_extractive_answer_stores_no_prompt_because_none_was_sent(scene) -> None:
    db, client_as, patient, _llm = scene
    rag_llm.set_client(None)
    body = _ask(client_as("operador.norte"), patient_id=patient["id"]).json()
    assert body["mode"] == "extractivo" and body["degraded_reason"] == "llm_not_configured"
    row = db.get(RagSuggestion, uuid.UUID(body["suggestion_id"]))
    assert row.prompt_sent == []
    detail = client_as("operador.norte").get(f"/api/v1/rag/suggestions/{body['suggestion_id']}").json()
    assert detail["prompt_sent"] == []


def test_a_rate_limited_model_falls_back_and_says_why(scene) -> None:
    _db, client_as, _patient, _llm = scene
    rag_llm.set_client(BrokenLlm("rate_limited"))
    body = _ask(client_as("operador.norte")).json()
    assert body["mode"] == "extractivo" and body["degraded_reason"] == "llm_rate_limited"
    rag_llm.set_client(FakeLlm())
    assert _ask(client_as("operador.norte")).json()["degraded_reason"] is None


def test_the_losing_reviewer_of_a_race_gets_409_and_no_history_chunk(scene) -> None:
    from sqlalchemy import update

    from app.models.enums import RagSuggestionStatus
    from app.services import rag_suggestions
    from app.services.errors import ConflictError

    db, client_as, patient, _llm = scene
    suggestion_id = uuid.UUID(_ask(client_as("operador.norte"), patient_id=patient["id"]).json()["suggestion_id"])
    stale = rag_suggestions.get_suggestion(db, suggestion_id)  # this request still sees "pendiente"
    assert stale.status == RagSuggestionStatus.PENDING
    # Another request signs first, behind this one's back.
    db.execute(
        update(RagSuggestion).where(RagSuggestion.id == suggestion_id).values(status=RagSuggestionStatus.REJECTED)
    )
    db.commit()
    actor = _user(db, "operador.norte")
    with pytest.raises(ConflictError):
        rag_suggestions.approve(db, stale, actor=actor, observaciones=None)
    with pytest.raises(ConflictError):
        rag_suggestions.reject(db, stale, actor=actor, motivo="tarde", observaciones=None)
    assert db.scalars(select(RagChunk).where(RagChunk.base == RagBase.HISTORY)).first() is None
    assert db.get(RagSuggestion, suggestion_id).status == RagSuggestionStatus.REJECTED


def test_history_approved_while_ml_was_down_is_backfilled_and_still_retrievable(scene) -> None:
    from app.services.rag.ingest import embed_missing
    from app.services.rag.retrieval import retrieve

    db, client_as, patient, _llm = scene
    doctor = client_as("operador.norte")
    suggestion = _ask(doctor, patient_id=patient["id"]).json()["suggestion_id"]
    fake_ml = FakeEmbedMl()
    ml_client.set_client(None)  # restores the HTTP client; point it at a down fake instead
    from tests.conftest import _DownMlClient

    ml_client.set_client(_DownMlClient())
    assert doctor.post(f"/api/v1/rag/suggestions/{suggestion}/approve", json={}).status_code == 200
    chunk = db.scalars(select(RagChunk).where(RagChunk.base == RagBase.HISTORY)).one()
    assert chunk.embedding is None
    assert embed_missing(db, history_only=True) == 0  # ml still down: nothing happens, nothing breaks

    ml_client.set_client(fake_ml)
    pseudonym = pseudonymization.pseudonym_for(patient["id"])
    result = retrieve(db, "respuesta aprobada glucagón dextrosa", patient_pseudonym=pseudonym, k=5)
    assert chunk.chunk_id in [h.chunk.chunk_id for h in result.hits]
    db.refresh(chunk)
    assert chunk.embedding is not None and len(chunk.embedding) == 384  # lazily backfilled


def test_mmr_keeps_a_history_chunk_without_a_vector(db) -> None:
    from app.services.rag.retrieval import retrieve

    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    text = "retinopatía diabética grado dos control anual de fondo de ojo"
    db.add(RagChunk(chunk_id="HS-n-0", doc_id="HS-n", base=RagBase.HISTORY, doc_type="s", estado="aprobado",
                    patient_pseudonym="pz-aaaaaaaaaaaaaaaa", source="s", title="t", keywords=[], text=text,
                    word_count=9))
    db.commit()
    import app.services.rag.retrieval as retrieval_module

    original = retrieval_module.embed_missing
    retrieval_module.embed_missing = lambda *_a, **_k: 0  # keep it without a vector
    try:
        result = retrieve(db, "retinopatía diabética fondo de ojo", method="hybrid_mmr", k=3,
                          patient_pseudonym="pz-aaaaaaaaaaaaaaaa")
    finally:
        retrieval_module.embed_missing = original
    assert result.hits and result.hits[0].chunk.chunk_id == "HS-n-0"


def test_a_rare_shared_word_in_an_off_topic_question_makes_the_assistant_abstain(scene, monkeypatch) -> None:
    from app.services.rag import retrieval
    from tests.rag_helpers import toy_vector

    _db, client_as, _patient, llm = scene
    monkeypatch.setattr(get_settings(), "rag_candidates", 1)
    monkeypatch.setattr(retrieval, "embed_query", lambda _q: toy_vector("zzz yyy xxx www"))
    body = _ask(client_as("operador.norte"), question="¿Quién ganó el mundial de fútbol 2014 con glucagón y dextrosa?").json()
    assert body["mode"] == "abstencion" and body["sources"] == [] and llm.calls == []
