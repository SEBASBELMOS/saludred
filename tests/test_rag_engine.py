# ruff: noqa: F811
"""Pure RAG engine: tokenizer, BM25/TF-IDF, fusion, chunking, corpus parsing, ingest."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.enums import RagBase
from app.models.rag import RagChunk
from app.services import ml_client
from app.services.rag import chunking, fusion, store
from app.services.rag.corpus import CorpusFormatError, load_corpus, parse_document
from app.services.rag.lexical import Bm25Index, TfidfIndex
from app.services.rag.retrieval import retrieve
from app.services.rag.text import tokenize
from tests.rag_helpers import (
    CORPUS_TEXT,
    FakeEmbedMl,
    load_tiny_corpus,
    tiny_corpus,
    toy_vector,
)
from tests.test_bed_rules import db  # noqa: F401  (fixture reuse)


def test_tokenizer_matches_the_notebook_semantics() -> None:
    assert tokenize("¿Cuál es la meta de HbA1c en un adulto mayor frágil?") == [
        "meta", "hba1c", "adulto", "mayor", "fragil",
    ]
    assert tokenize("HbA1c ≥ 6,5 % y glucosa de 126 mg/dL") == ["hba1c", "6,5", "glucosa", "126", "mg", "dl"]


def test_bm25_ranks_the_relevant_passage_first() -> None:
    docs = [
        "La metformina es el fármaco de primera línea en diabetes tipo 2.",
        "Hipoglucemia es glucosa menor de 70 mg/dL con temblor y sudoración.",
        "El triage clasifica a los pacientes de urgencias por prioridad.",
    ]
    scores = Bm25Index(docs).scores("temblor y sudoración con glucosa baja")
    assert scores.index(max(scores)) == 1
    assert scores[2] == 0.0  # no shared token -> no evidence


def test_tfidf_is_a_cosine_in_unit_range() -> None:
    docs = ["glucosa en ayunas diabetes", "triage urgencias prioridad", "glucosa glucosa glucosa"]
    scores = TfidfIndex(docs).scores("glucosa diabetes")
    assert scores.index(max(scores)) == 0
    assert all(0.0 <= s <= 1.0 + 1e-9 for s in scores)


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = fusion.rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert [k for k, _ in fused][:2] == ["a", "b"]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)
    # c and d appear in one list each: below the ones both lists agree on.
    assert {k for k, _ in fused[2:]} == {"c", "d"}


def test_mmr_trades_relevance_for_diversity() -> None:
    vectors = {"a": [1.0, 0.0], "a2": [0.99, 0.14], "b": [0.6, 0.8]}
    relevance = {"a": 0.9, "a2": 0.88, "b": 0.6}

    def sim(x: str, y: str) -> float:
        return fusion.cosine(vectors[x], vectors[y])

    pure = fusion.mmr(["a", "a2", "b"], relevance.get, sim, k=2, lam=1.0)
    diverse = fusion.mmr(["a", "a2", "b"], relevance.get, sim, k=2, lam=0.5)
    assert pure == ["a", "a2"]
    assert diverse == ["a", "b"]


def test_paragraph_chunking_keeps_a_short_paragraph_whole_and_splits_a_long_one() -> None:
    sentence = "Esta frase tiene exactamente diez palabras para contar bien aqui."
    long_paragraph = " ".join([sentence] * 12)  # 120 words, 12 sentences
    short = "Muy corto."
    result = chunking.chunk_document(f"{short}\n\n{long_paragraph}")
    assert result.paragraph_count == 2
    assert result.short_paragraphs == [0]
    assert result.chunks[0].text == short and result.chunks[0].paragraphs == (0,)
    rest = result.chunks[1:]
    assert len(rest) == 2 and all(c.word_count <= chunking.MAX_WORDS for c in rest)
    assert all(c.paragraphs == (1,) for c in rest)
    assert all(c.text.endswith("aqui.") for c in rest)  # split on sentence borders


def test_a_single_huge_sentence_falls_back_to_overlapping_windows() -> None:
    result = chunking.chunk_document(" ".join(f"w{i}" for i in range(150)))
    first, second = result.chunks[0].text.split(), result.chunks[1].text.split()
    assert len(first) == chunking.WINDOW
    assert first[-chunking.OVERLAP:] == second[: chunking.OVERLAP]


def test_fixed_chunking_ignores_paragraph_borders_but_remembers_them() -> None:
    body = "\n\n".join(" ".join(f"p{p}w{i}" for i in range(50)) for p in range(3))
    result = chunking.chunk_document(body, "fixed")
    assert [c.word_count for c in result.chunks][:2] == [60, 60]
    assert result.chunks[0].paragraphs == (0, 1)
    with pytest.raises(ValueError):
        chunking.chunk_document(body, "nope")


def test_corpus_parser_reads_front_matter_and_rejects_bad_files(tmp_path) -> None:
    doc = parse_document(CORPUS_TEXT["G01"])
    assert (doc.id, doc.base, doc.year, doc.keywords) == ("G01", "general", 2024, ["prueba"])
    assert doc.body.count("\n\n") == 1
    with pytest.raises(CorpusFormatError):
        parse_document("sin front matter")
    with pytest.raises(CorpusFormatError):
        parse_document("---\nid: X1\nbase: general\n---\ncuerpo")  # title/publisher/doc_type missing
    with pytest.raises(CorpusFormatError):
        parse_document(CORPUS_TEXT["G01"].replace("base: general", "base: historia"))
    (tmp_path / "triage").mkdir()
    (tmp_path / "triage" / "G01.md").write_text(CORPUS_TEXT["G01"], encoding="utf-8")
    with pytest.raises(CorpusFormatError):  # folder says triage, front matter says general
        load_corpus(tmp_path)


def test_corpus_loader_skips_index_files(tmp_path) -> None:
    (tmp_path / "general").mkdir()
    (tmp_path / "general" / "G01.md").write_text(CORPUS_TEXT["G01"], encoding="utf-8")
    (tmp_path / "general" / "SOURCES.md").write_text("| id | title |\n|---|---|\n", encoding="utf-8")
    assert [d.id for d in load_corpus(tmp_path)] == ["G01"]


def test_the_shipped_corpus_loads() -> None:
    """Synthetic corpora never exercised the real folder; this guards what ingest actually reads."""

    root = Path(__file__).resolve().parents[1] / "rag" / "corpus"
    if not root.is_dir():
        pytest.skip("corpus not present in this checkout")
    documents = load_corpus(root)
    bases = {d.base for d in documents}
    assert bases == {"general", "triage"}
    assert len(documents) >= 50


def test_ingest_a_temp_corpus_is_idempotent_and_embeds_only_what_changed(db, tmp_path) -> None:
    fake = FakeEmbedMl()
    ml_client.set_client(fake)
    for doc_id, text in CORPUS_TEXT.items():
        folder = tmp_path / ("general" if doc_id.startswith("G") else "triage")
        folder.mkdir(exist_ok=True)
        (folder / f"{doc_id}.md").write_text(text, encoding="utf-8")
    documents = load_corpus(tmp_path)
    assert [d.id for d in documents] == ["G01", "G02", "T01"]

    from app.services.rag.ingest import ingest_corpus

    summary = ingest_corpus(db, documents)
    rows = {r.chunk_id: r for r in db.scalars(select(RagChunk))}
    assert set(rows) == {"G01-0", "G01-1", "G02-0", "T01-0", "T01-1"}
    assert rows["G01-0"].base == RagBase.GENERAL and rows["T01-0"].base == RagBase.TRIAGE
    assert rows["G01-0"].estado == "vigente" and rows["G01-0"].patient_pseudonym is None
    assert len(rows["G01-0"].embedding) == 384
    assert summary.per_base["general"].chunks == 3 and summary.per_base["triage"].documents == 1
    assert summary.embedded_now == 5

    again = ingest_corpus(db, documents)
    assert again.embedded_now == 0 and db.query(RagChunk).count() == 5
    assert sum(len(call) for call in fake.calls) == 5

    # --reset removes guideline chunks only, never a patient's approved history.
    db.add(RagChunk(chunk_id="HS-x-0", doc_id="HS-x", base=RagBase.HISTORY, doc_type="s", estado="aprobado",
                    patient_pseudonym="pz-aaaaaaaaaaaaaaaa", source="s", title="t", keywords=[],
                    text="texto", word_count=1))
    db.commit()
    ingest_corpus(db, documents[:1], reset=True)
    assert {r.chunk_id for r in db.scalars(select(RagChunk))} == {"G01-0", "G01-1", "HS-x-0"}


def test_ingest_leaves_the_table_untouched_when_embeddings_fail(db) -> None:
    from app.services.errors import MlServiceUnavailableError
    from app.services.rag.ingest import ingest_corpus

    with pytest.raises(MlServiceUnavailableError):  # default test client: ml is down
        ingest_corpus(db, tiny_corpus())
    assert db.query(RagChunk).count() == 0
    ingest_corpus(db, tiny_corpus(), embed=False)  # lexical-only ingest still works
    assert db.query(RagChunk).count() == 5


def test_methods_rank_by_meaning_and_hybrid_fuses(db) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    question = "paciente con hipoglucemia inconsciente glucagón"
    for method in ("tfidf", "bm25", "dense", "hybrid", "hybrid_mmr"):
        result = retrieve(db, question, method=method, k=3)
        assert result.hits and result.hits[0].chunk.doc_id == "G01", method
        assert not result.degraded
    hybrid = retrieve(db, question, method="hybrid", k=3)
    assert hybrid.hits[0].score == pytest.approx(2 / 61)  # first in BOTH rankings


def test_mmr_pool_is_diversified_but_never_leaves_the_pool(db) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    question = "glucosa paciente urgencias atención"
    hybrid = retrieve(db, question, method="hybrid", k=5)
    mmr = retrieve(db, question, method="hybrid_mmr", k=3, candidates=5)
    assert {h.chunk.chunk_id for h in mmr.hits} <= {h.chunk.chunk_id for h in hybrid.hits}
    assert len(mmr.hits) == 3


def test_pre_filter_never_returns_another_patients_or_unapproved_history(db) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    text = "retinopatía diabética grado dos control anual de fondo de ojo"
    for chunk_id, pseudonym, estado in [
        ("HS-a-0", "pz-aaaaaaaaaaaaaaaa", "aprobado"),
        ("HS-b-0", "pz-bbbbbbbbbbbbbbbb", "aprobado"),
        ("HS-c-0", "pz-aaaaaaaaaaaaaaaa", "pendiente"),
    ]:
        db.add(RagChunk(chunk_id=chunk_id, doc_id=chunk_id[:-2], base=RagBase.HISTORY, doc_type="s",
                        estado=estado, patient_pseudonym=pseudonym, source="s", title="t", keywords=[],
                        text=text, word_count=9, embedding=toy_vector(text)))
    db.commit()

    for method in ("bm25", "dense", "hybrid", "hybrid_mmr"):
        mine = retrieve(db, "retinopatía diabética fondo de ojo", method=method, k=10,
                        patient_pseudonym="pz-aaaaaaaaaaaaaaaa")
        ids = {h.chunk.chunk_id for h in mine.hits}
        assert "HS-a-0" in ids and not ids & {"HS-b-0", "HS-c-0"}, method
        nobody = retrieve(db, "retinopatía diabética fondo de ojo", method=method, k=10)
        assert not {h.chunk.chunk_id for h in nobody.hits} & {"HS-a-0", "HS-b-0", "HS-c-0"}, method

    index = store.get_index(db)
    allowed = store.allowed_chunk_ids(index, base_filter="triage", patient_pseudonym="pz-aaaaaaaaaaaaaaaa")
    assert allowed == {"T01-0", "T01-1", "HS-a-0"}


def test_retrieval_degrades_to_lexical_when_the_embedding_service_is_down(db) -> None:
    load_tiny_corpus(db, embed=False)  # ingest without ml, like an outage
    result = retrieve(db, "hipoglucemia inconsciente glucagón", method="hybrid_mmr", k=3)
    assert result.degraded and result.max_cosine is None
    assert result.hits[0].chunk.doc_id == "G01"
    assert retrieve(db, "hipoglucemia", method="bm25", k=3).degraded is False


def test_abstention_signal_needs_both_weak_scores(db) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    on_topic = retrieve(db, "hipoglucemia glucagón inconsciente", k=3)
    off_topic = retrieve(db, "quién ganó el mundial de fútbol de 2014", k=3)
    assert on_topic.has_evidence()
    assert not off_topic.has_evidence()
    assert off_topic.max_bm25 < 1.5 and (off_topic.max_cosine or 0) < 0.45


def test_pgvector_query_uses_the_cosine_operator() -> None:
    sql = str(
        store.dense_statement([0.1] * 384, ["G01-0"]).compile(dialect=postgresql.dialect())
    )
    assert "<=>" in sql and "vector(384)" in sql and "ORDER BY" in sql


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_evidence_needs_the_embeddings_to_agree_with_a_lexical_match() -> None:
    from app.services.rag.retrieval import RetrievalResult

    def result(cos, bm25, agrees, terms=4):  # noqa: ANN001, ANN202
        return RetrievalResult([], "hybrid_mmr", cos is None, cos, bm25, agrees, terms)

    assert result(0.6, 0.0, False).has_evidence()  # close embedding alone is enough
    assert result(0.2, 2.5, True).has_evidence()  # strong BM25 backed by the embeddings
    assert not result(0.2, 2.5, False).has_evidence()  # one rare shared word: BM25 alone is not enough
    assert not result(0.2, 1.0, True).has_evidence()
    assert result(0.2, 0.1, False).has_evidence(extra_bm25=2.0)  # the patient's own summary matched
    # Degraded: BM25 only, normalized by the number of query terms.
    assert result(None, 1.6, False, terms=3).has_evidence()
    assert not result(None, 1.6, False, terms=12).has_evidence()


def test_an_off_topic_question_that_shares_one_rare_word_is_not_evidence(db, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.rag import retrieval

    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    monkeypatch.setattr(get_settings(), "rag_candidates", 1)
    # An embedding far from every chunk, as a real model would give an off-topic question.
    monkeypatch.setattr(retrieval, "embed_query", lambda _q: toy_vector("zzz yyy xxx www"))
    question = "¿Quién ganó el mundial de fútbol 2014 con glucagón y dextrosa?"
    result = retrieve(db, question, method="hybrid_mmr", k=3)
    assert result.max_bm25 >= 1.5  # the rare word matches lexically...
    assert not result.bm25_top_in_dense_top  # ...but the embeddings do not support it
    assert not result.has_evidence()


def test_hybrid_never_pads_the_context_with_junk(db) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    result = retrieve(db, "hipoglucemia inconsciente glucagón", method="hybrid", k=5)
    assert 1 <= len(result.hits) < 5
    assert all(h.chunk.doc_id == "G01" or h.score > 0 for h in result.hits)
    assert retrieve(db, "mundial de fútbol", method="hybrid", k=5).hits == []
    assert retrieve(db, "mundial de fútbol", method="dense", k=5).hits == []


def test_ingest_without_reset_drops_chunks_of_removed_documents_but_never_history(db) -> None:
    from app.services.rag.ingest import ingest_corpus

    ml_client.set_client(FakeEmbedMl())
    documents = tiny_corpus()
    ingest_corpus(db, documents)
    db.add(RagChunk(chunk_id="HS-x-0", doc_id="HS-x", base=RagBase.HISTORY, doc_type="s", estado="aprobado",
                    patient_pseudonym="pz-aaaaaaaaaaaaaaaa", source="s", title="t", keywords=[],
                    text="texto", word_count=1))
    db.commit()
    summary = ingest_corpus(db, [d for d in documents if d.id != "G02"])  # G02 left the corpus
    assert summary.removed == 1
    assert {r.doc_id for r in db.scalars(select(RagChunk))} == {"G01", "T01", "HS-x"}


def test_ingest_backfills_history_embeddings(db) -> None:
    from app.services.rag.ingest import ingest_corpus

    ml_client.set_client(FakeEmbedMl())
    db.add(RagChunk(chunk_id="HS-y-0", doc_id="HS-y", base=RagBase.HISTORY, doc_type="s", estado="aprobado",
                    patient_pseudonym="pz-aaaaaaaaaaaaaaaa", source="s", title="t", keywords=[],
                    text="control anual de fondo de ojo", word_count=6))
    db.commit()
    summary = ingest_corpus(db, tiny_corpus())
    assert summary.history_backfilled == 1
    assert db.scalars(select(RagChunk).where(RagChunk.chunk_id == "HS-y-0")).one().embedding is not None


def test_groq_payload_retry_without_reasoning_format_and_think_stripping(monkeypatch) -> None:
    import httpx

    from app.services.errors import LlmUnavailableError
    from app.services.rag.llm import GroqClient

    sent: list[dict] = []
    replies = [400, 200]

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        sent.append(dict(json))
        status = replies.pop(0) if replies else 200
        body = {"choices": [{"message": {"content": "<think>x</think>Respuesta <think>sin cerrar"}}]}
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    client = GroqClient("k", "qwen/qwen3-32b", "https://api.groq.test/openai/v1", 30.0, 600)
    assert client.complete([{"role": "user", "content": "hola"}]) == "Respuesta"
    assert sent[0]["max_tokens"] == 600 and sent[0]["reasoning_format"] == "hidden" and sent[0]["temperature"] == 0
    assert "reasoning_format" not in sent[1] and len(sent) == 2

    def limited(url, headers=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        return httpx.Response(429, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", limited)
    with pytest.raises(LlmUnavailableError) as caught:
        client.complete([])
    assert caught.value.reason == "rate_limited"


# ---------------------------------------------------------------------------
# Groq client: 429 back-off, optional parameters, separate judge model
# ---------------------------------------------------------------------------


def _groq(monkeypatch, statuses, headers=None, model="qwen/qwen3.8-27b", **kwargs):  # noqa: ANN001, ANN202
    import httpx

    from app.services.rag.llm import GroqClient

    sent: list[dict] = []
    waits: list[float] = []
    queue = list(statuses)

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        sent.append(dict(json))
        status = queue.pop(0) if queue else 200
        reply = {"choices": [{"message": {"content": "ok"}}]}
        return httpx.Response(status, json=reply, headers=hdrs.pop(0) if hdrs else None,
                              request=httpx.Request("POST", url))

    hdrs = list(headers or [])
    monkeypatch.setattr(httpx, "post", fake_post)
    client = GroqClient("k", model, "https://api.groq.test/openai/v1", 30.0, 600,
                        sleep=waits.append, **kwargs)
    return client, sent, waits


def test_429_honors_retry_after_then_backs_off_exponentially(monkeypatch) -> None:
    client, sent, waits = _groq(monkeypatch, [429, 429, 200], headers=[{"retry-after": "7"}, {}], max_retries=5)
    assert client.complete([]) == "ok"
    assert waits == [7.0, 4.0]  # header first; the second attempt falls back to the schedule slot
    assert client.rate_limit_hits == 2 and len(sent) == 3


def test_429_backoff_schedule_and_giving_up(monkeypatch) -> None:
    from app.services.errors import LlmUnavailableError

    client, sent, waits = _groq(monkeypatch, [429] * 10, max_retries=5)
    with pytest.raises(LlmUnavailableError) as caught:
        client.complete([])
    assert caught.value.reason == "rate_limited"
    assert waits == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert len(sent) == 6 and client.rate_limit_hits == 6

    no_retry, _sent, no_waits = _groq(monkeypatch, [429], max_retries=0)
    with pytest.raises(LlmUnavailableError):
        no_retry.complete([])
    assert no_waits == []


def test_unsupported_optional_parameters_are_dropped_once_and_remembered(monkeypatch) -> None:
    client, sent, _waits = _groq(
        monkeypatch, [400, 400, 200], model="openai/gpt-oss-20b", reasoning_effort="low"
    )
    assert client.complete([]) == "ok"
    assert "reasoning_format" in sent[0] and sent[0]["reasoning_effort"] == "low"
    assert "reasoning_format" not in sent[1] and "reasoning_effort" in sent[1]
    assert "reasoning_format" not in sent[2] and "reasoning_effort" not in sent[2]
    sent.clear()
    client.complete([])
    assert len(sent) == 1 and "reasoning_effort" not in sent[0]  # no 400 round trips next time


def test_throttle_pauses_after_every_call() -> None:
    from app.services.rag.llm import ThrottledClient

    waits: list[float] = []
    inner = FakeLlmForThrottle()
    client = ThrottledClient(inner, 2.5, sleep=waits.append)
    client.complete([])
    client.complete([])
    assert waits == [2.5, 2.5] and client.model == "m" and client.rate_limit_hits == 3


class FakeLlmForThrottle:
    model = "m"
    rate_limit_hits = 3

    def complete(self, _messages: list[dict[str, str]]) -> str:
        return "x"


def test_the_judge_uses_its_own_model(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.rag import llm

    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", "k")
    monkeypatch.setattr(settings, "groq_model", "qwen/qwen3.8-27b")
    monkeypatch.setattr(settings, "rag_judge_model", None)
    llm.set_judge_client()  # conftest leaves the default factory in place
    generator = llm.get_client(max_retries=5)
    assert generator is None or generator.model == "qwen/qwen3.8-27b"  # conftest forces "no model"
    assert llm.get_judge_client().model == "qwen/qwen3.8-27b"  # unset: the generator model
    monkeypatch.setattr(settings, "rag_judge_model", "openai/gpt-oss-20b")
    judge = llm.get_judge_client()
    assert judge.model == "openai/gpt-oss-20b" and judge._reasoning_effort == "low"
    assert judge._max_tokens >= llm.JUDGE_MIN_TOKENS
    assert llm.get_judge_client(model="other/model").model == "other/model"  # --judge-model wins
    assert llm.build_client("qwen/qwen3.8-27b")._reasoning_effort is None


# ---------------------------------------------------------------------------
# Weighted RRF and prompt size
# ---------------------------------------------------------------------------


def test_weighted_rrf_lets_the_heavier_ranking_win_a_disagreement() -> None:
    lexical, dense = ["a", "b", "c"], ["c", "b", "a"]
    assert [k for k, _ in fusion.rrf([lexical, dense])] == ["a", "c", "b"]  # equal weights: a and c tie, first seen wins
    heavy_dense = fusion.rrf([lexical, dense], weights=[1.0, 3.0])
    assert [k for k, _ in heavy_dense] == ["c", "b", "a"]
    assert heavy_dense[0][1] == pytest.approx(1 / 63 + 3 / 61)
    heavy_lexical = fusion.rrf([lexical, dense], weights=[3.0, 1.0])
    assert [k for k, _ in heavy_lexical] == ["a", "b", "c"]
    assert fusion.rrf([lexical, dense], weights=None) == fusion.rrf([lexical, dense], weights=[1.0, 1.0])


def test_retrieve_applies_the_weights_for_one_call_and_the_settings_by_default(db, monkeypatch) -> None:
    from app.core.config import get_settings

    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    question = "paciente con hipoglucemia inconsciente glucagón"
    plain = retrieve(db, question, method="hybrid", k=3)
    assert plain.hits[0].score == pytest.approx(2 / 61)  # first in both, weights 1 and 1
    weighted = retrieve(db, question, method="hybrid", k=3, dense_weight=3.0, lexical_weight=1.0)
    assert weighted.hits[0].score == pytest.approx(4 / 61)
    monkeypatch.setattr(get_settings(), "rag_rrf_dense_weight", 2.0)
    assert retrieve(db, question, method="hybrid", k=3).hits[0].score == pytest.approx(3 / 61)
    # The per-call override beats the setting.
    assert retrieve(db, question, method="hybrid", k=3, dense_weight=1.0).hits[0].score == pytest.approx(2 / 61)


def test_nothing_truncates_the_context_and_max_tokens_only_caps_the_completion(monkeypatch) -> None:
    import httpx

    from app.services.rag.llm import GroqClient
    from app.services.rag.prompt import PromptSource, build_messages

    long_text = " ".join(f"palabra{i}" for i in range(400))
    sources = [
        PromptSource(n=i, chunk_id=f"X-{i}", doc_id="X", title="t" * 300, url=None, score=0.5,
                     label="fuente", text=f"{long_text} fin{i}")
        for i in range(1, 6)
    ]
    messages = build_messages("pregunta", sources)
    content = messages[1]["content"]
    assert all(f"fin{i}" in content for i in range(1, 6)) and content.count("palabra399") == 5

    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001, ANN202
        sent.append(dict(json))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    GroqClient("k", "m", "https://api.groq.test/v1", 30.0, 600).complete(messages)
    assert sent[0]["messages"] == messages and sent[0]["max_tokens"] == 600
