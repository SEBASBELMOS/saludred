# ruff: noqa: F811
"""Evaluation: retrieval metrics, abstention, judge metrics and the report."""

from __future__ import annotations

import json
import re

import pytest

from app.services import ml_client
from app.services.rag import evaluation, llm
from app.services.rag.judge import Judge, JudgeError, parse_json, split_sentences
from app.services.rag.report import render_markdown, render_sweep_markdown
from app.services.rag.retrieval import embed_query
from app.services.rag.store import ChunkMeta
from tests.rag_helpers import FakeEmbedMl, load_tiny_corpus, tiny_corpus
from tests.test_bed_rules import db  # noqa: F401  (fixture reuse)

QUESTIONS_GENERAL = """
- id: Q01
  question: "¿Qué hago con una hipoglucemia en un paciente inconsciente? glucagón dextrosa"
  expected_doc: G01
  expected_paragraph: 1
  reference_answer: "Se usa glucagón o dextrosa intravenosa. Se activa la atención de urgencias."
- id: Q02
  question: "¿Con qué glucosa en ayunas se diagnostica diabetes?"
  expected_doc: G02
  expected_paragraph: 0
  reference_answer: "Con una glucosa en ayunas mayor o igual a 126 mg/dL."
- id: Q03
  question: "¿Quién ganó el mundial de fútbol de 2014?"
  expected_doc: null
  expected_paragraph: null
  reference_answer: ABSTAIN
"""
QUESTIONS_TRIAGE = """
- id: Q04
  question: "triage I atención inmediata riesgo vital reanimación"
  expected_doc: T01
  expected_paragraph: 0
  reference_answer: "Requiere atención inmediata por riesgo vital."
"""


@pytest.fixture
def questions(tmp_path):
    (tmp_path / "questions_general.yaml").write_text(QUESTIONS_GENERAL, encoding="utf-8")
    (tmp_path / "questions_triage.yaml").write_text(QUESTIONS_TRIAGE, encoding="utf-8")
    return evaluation.load_questions(tmp_path)


def test_questions_load_with_their_dataset(questions) -> None:
    assert [(q.id, q.dataset, q.is_abstention) for q in questions] == [
        ("Q01", "general", False), ("Q02", "general", False), ("Q03", "general", True), ("Q04", "triage", False),
    ]


def test_expected_chunks_follow_the_chunker_for_both_strategies() -> None:
    documents = tiny_corpus()
    paragraph = evaluation.expected_chunks(documents, "paragraph")
    assert paragraph[("G01", 0)] == {"G01-0"} and paragraph[("G01", 1)] == {"G01-1"}  # 1:1 holds
    assert evaluation.non_one_to_one_documents(documents) == []
    fixed = evaluation.expected_chunks(documents, "fixed")
    assert fixed[("G01", 0)] and fixed[("G01", 1)]


def test_score_ranking_computes_hits_and_reciprocal_ranks() -> None:
    def chunk(doc: str, i: int) -> ChunkMeta:
        return ChunkMeta(f"{doc}-{i}", doc, "general", "guia", "vigente", None, "s", "t", None, "x", 1)

    question = evaluation.Question("general", "Q", "q", "G02", 1, "r")
    ranking = [chunk("G01", 0), chunk("G02", 0), chunk("G02", 1)]
    score = evaluation.score_ranking(ranking, question, {"G02-1"}, [1, 3])
    assert score["doc_hits"] == {1: False, 3: True} and score["rr_doc"] == 0.5
    assert score["chunk_hits"] == {1: False, 3: True} and score["rr_chunk"] == pytest.approx(1 / 3)
    no_target = evaluation.score_ranking(ranking, question, None, [1, 3])
    assert no_target["chunk_hits"] is None and no_target["rr_chunk"] is None
    agg = evaluation.aggregate([score, no_target], [1, 3])
    assert agg["n"] == 2 and agg["n_chunk_level"] == 1 and agg["recall_doc"][3] == 1.0
    assert agg["recall_chunk"][1] == 0.0


def test_retrieval_and_abstention_metrics(db, questions) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    results = evaluation.run_evaluation(db, questions, tiny_corpus(), ks=[1, 3, 5])
    json.dumps(results)  # serializable as results.json

    assert results["questions"] == {"total": 4, "answerable": 3, "out_of_scope": 1}
    assert set(results["retrieval"]) == {"tfidf", "bm25", "dense", "hybrid", "hybrid_mmr"}
    for method, scopes in results["retrieval"].items():
        assert set(scopes) == {"overall", "general", "triage"}, method
        assert scopes["overall"]["n"] == 3 and scopes["general"]["n"] == 2
        assert scopes["overall"]["recall_doc"][5] == 1.0, method
        assert scopes["overall"]["mrr_doc"] > 0.5, method
        assert scopes["overall"]["n_chunk_level"] == 3
    # Q01 expects paragraph 1 of G01 = chunk G01-1, found first by every method.
    assert results["retrieval"]["bm25"]["general"]["recall_chunk"][1] >= 0.5

    abstention = results["abstention"]
    assert abstention["n_out_of_scope"] == 1 and abstention["accuracy"] == 1.0
    assert abstention["false_abstentions"] == 0 and abstention["false_abstention_rate"] == 0.0
    assert results["judge"] is None and results["corpus"]["chunks"]["general"] == 3

    markdown = render_markdown(results)
    assert "Recall@1" in markdown and "Híbrido + MMR" in markdown and "Exactitud de abstención" in markdown


def test_the_evaluation_refuses_to_run_without_embeddings_or_chunks(db, questions) -> None:
    with pytest.raises(evaluation.EvaluationError, match="empty"):
        evaluation.run_evaluation(db, questions, tiny_corpus(), ks=[1])
    load_tiny_corpus(db, embed=False)
    with pytest.raises(evaluation.EvaluationError, match="embedding"):  # conftest: ml is down
        evaluation.run_evaluation(db, questions, tiny_corpus(), ks=[1])


class JudgeLlm:
    """Answers generation prompts with a cited sentence and judge prompts with JSON."""

    model = "fake-judge"

    def __init__(self) -> None:
        self.judge_calls = 0

    @staticmethod
    def _count(block: str) -> int:
        return len(re.findall(r"^\[\d+\] ", block, re.MULTILINE))

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not messages[0]["content"].startswith("You are a strict evaluator"):
            return "Se usa glucagón o dextrosa intravenosa [1]."
        self.judge_calls += 1
        prompt = messages[1]["content"]
        if prompt.startswith("Split the ANSWER"):
            return '```json\n{"claims": [{"claim": "a", "supported": true}, {"claim": "b", "supported": false}]}\n```'
        if prompt.startswith("Write"):
            return '{"questions": ["¿Qué hago con una hipoglucemia?", "hipoglucemia glucagón"]}'
        if prompt.startswith("For each numbered CONTEXT chunk"):
            count = self._count(prompt.split("CONTEXT:\n", 1)[1])
            return json.dumps({"verdicts": [True] + [False] * (count - 1)})
        reference_block = prompt.split("REFERENCE:\n", 1)[1].split("\n\nCONTEXT:", 1)[0]
        return json.dumps({"verdicts": [True] * self._count(reference_block)})


def test_judge_metrics_follow_the_ragas_definitions(db, questions) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    fake = JudgeLlm()
    llm.set_client(fake)
    judge = Judge(fake, embed_query, sleep=lambda _s: None)
    results = evaluation.run_evaluation(db, questions, tiny_corpus(), ks=[1, 3], judge=judge, generator=fake)

    judged = results["judge"]
    assert judged["model"] == "fake-judge" and judged["overall"]["n_judged"] == 3
    overall = judged["overall"]
    assert overall["faithfulness"] == 0.5  # one of two claims supported
    assert overall["context_recall"] == 1.0  # every reference sentence attributable
    # Only the first chunk is relevant: precision@1 = 1, so the rank-weighted mean is 1.
    assert overall["context_precision"] == 1.0
    assert 0.0 <= overall["answer_relevancy"] <= 1.0 and overall["answer_relevancy"] > 0
    assert set(judged["by_dataset"]) == {"general", "triage"}
    markdown = render_markdown(results)
    assert "Fidelidad" in markdown and "bajo la meta" in markdown  # faithfulness 0.5 < 0.75
    json.dumps(results)


def test_context_precision_is_rank_weighted() -> None:
    class Verdicts:
        model = "m"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return '{"verdicts": [false, true, true]}'

    judge = Judge(Verdicts(), lambda _t: None, sleep=lambda _s: None)
    # (1/2 + 2/3) / 2
    assert judge.context_precision("q", "r", ["a", "b", "c"]) == pytest.approx((0.5 + 2 / 3) / 2)


def test_the_judge_retries_unparsable_replies_and_then_gives_up() -> None:
    class Flaky:
        model = "m"

        def __init__(self, replies: list[str]) -> None:
            self.replies = replies
            self.calls = 0

        def complete(self, _messages: list[dict[str, str]]) -> str:
            self.calls += 1
            return self.replies[min(self.calls - 1, len(self.replies) - 1)]

    flaky = Flaky(["no json", '{"verdicts": [true, true]}', ])
    judge = Judge(flaky, lambda _t: None, sleep=lambda _s: None)
    assert judge.context_recall("Una frase. Otra frase.", ["ctx"]) == 1.0 and flaky.calls == 2

    wrong_shape = Flaky(['{"verdicts": [true]}'])  # 2 sentences, 1 verdict: always wrong
    with pytest.raises(JudgeError):
        Judge(wrong_shape, lambda _t: None, sleep=lambda _s: None).context_recall("Una. Dos.", ["ctx"])
    assert wrong_shape.calls == 4


def test_the_judge_reads_verdicts_strictly() -> None:
    class Claims:
        model = "m"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps({"claims": [{"supported": v} for v in (True, "true", "sí", 1, "false", "no", None, 0, "quizá")]})

    judge = Judge(Claims(), lambda _t: None, sleep=lambda _s: None)
    assert judge.faithfulness("a", ["c"]) == pytest.approx(4 / 9)


def test_json_and_sentence_helpers() -> None:
    assert parse_json('Claro:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("sin json")
    assert split_sentences("Primera frase.  Segunda frase? Tercera") == ["Primera frase.", "Segunda frase?", "Tercera"]


def _write_corpus(root) -> None:
    from tests.rag_helpers import CORPUS_TEXT

    for doc_id, text in CORPUS_TEXT.items():
        folder = root / ("general" if doc_id.startswith("G") else "triage")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{doc_id}.md").write_text(text, encoding="utf-8")


def test_the_two_clis_ingest_and_evaluate_end_to_end(db, tmp_path, monkeypatch, capsys) -> None:
    import contextlib
    import json

    from scripts import rag_eval, rag_ingest

    ml_client.set_client(FakeEmbedMl())
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    questions_dir = tmp_path / "eval"
    questions_dir.mkdir()
    (questions_dir / "questions_general.yaml").write_text(QUESTIONS_GENERAL, encoding="utf-8")
    (questions_dir / "questions_triage.yaml").write_text(QUESTIONS_TRIAGE, encoding="utf-8")
    for module in (rag_ingest, rag_eval):
        monkeypatch.setattr(module, "SessionLocal", lambda: contextlib.nullcontext(db))

    assert rag_ingest.main(["--corpus", str(corpus), "--reset"]) == 0
    out = capsys.readouterr().out
    assert "general" in out and "triage" in out and "embedded now: 5" in out
    assert rag_ingest.main(["--corpus", str(corpus)]) == 0
    assert "embedded now: 0" in capsys.readouterr().out  # idempotent
    assert rag_ingest.main(["--corpus", str(tmp_path / "missing")]) == 2

    results_dir = tmp_path / "out"
    code = rag_eval.main(
        ["--corpus", str(corpus), "--questions", str(questions_dir), "--out", str(results_dir), "--k", "1,3"]
    )
    assert code == 0
    saved = json.loads((results_dir / "results.json").read_text(encoding="utf-8"))
    assert saved["ks"] == [1, 3] and saved["retrieval"]["hybrid_mmr"]["overall"]["n"] == 3
    assert "Recall@3" in (results_dir / "RESULTS.md").read_text(encoding="utf-8")
    # --judge without a key is refused instead of silently skipped (conftest: no model).
    assert rag_eval.main(["--corpus", str(corpus), "--questions", str(questions_dir), "--judge"]) == 2


class ScriptedGenerator:
    """Generation client: one canned outcome per call, and a 429 counter like the real one."""

    model = "fake-generator"

    def __init__(self, outcomes: list[str]) -> None:
        self.outcomes = outcomes
        self.rate_limit_hits = 7

    def complete(self, _messages: list[dict[str, str]]) -> str:
        outcome = self.outcomes.pop(0)
        if outcome == "429":
            from app.services.errors import LlmUnavailableError

            raise LlmUnavailableError("limit", "rate_limited")
        return outcome


def test_the_report_counts_what_was_really_judged(db, questions) -> None:
    from app.services.rag.prompt import ABSTENTION_TEXT

    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    generator = ScriptedGenerator(["Se usa glucagón [1].", "429", ABSTENTION_TEXT])
    judge_llm = JudgeLlm()
    judge_llm.model = "fake-other-judge"
    judge = Judge(judge_llm, embed_query, sleep=lambda _s: None)
    results = evaluation.run_evaluation(
        db, questions, tiny_corpus(), ks=[1, 3], judge=judge, generator=generator, sleep_seconds=3.0
    )
    judged = results["judge"]
    overall = judged["overall"]
    assert (overall["n_questions"], overall["n_judged"], overall["n_degraded"], overall["n_abstained"]) == (3, 1, 1, 1)
    by_status = {r["id"]: r["status"] for r in judged["details"]}
    assert by_status == {"Q01": "judged", "Q02": "degraded", "Q04": "abstained"}
    degraded = next(r for r in judged["details"] if r["status"] == "degraded")
    assert degraded["degraded_reason"] == "llm_rate_limited"
    assert all(degraded[m] is None for m in evaluation.JUDGE_METRICS)  # nothing to grade
    abstained = next(r for r in judged["details"] if r["status"] == "abstained")
    assert abstained["faithfulness"] is None and abstained["context_recall"] is not None
    # Faithfulness comes from the single judged answer only (1 of 2 claims), never diluted.
    assert overall["n_faithfulness"] == 1 and overall["faithfulness"] == 0.5
    assert overall["n_context_recall"] == 2  # judged + abstained
    assert results["models"]["llm"] == "fake-generator" and results["models"]["judge"] == "fake-other-judge"
    assert results["run"]["sleep_seconds"] == 3.0 and results["run"]["rate_limit_hits"] == 7
    assert results["run"]["rrf_dense_weight"] == 1.0 and results["run"]["context_k"] == 3

    markdown = render_markdown(results)
    assert "Generador: `fake-generator` · juez: `fake-other-judge`" in markdown
    assert "Pausa entre llamadas: 3.0 s · respuestas HTTP 429 recibidas: 7" in markdown
    assert "Juzgadas con respuesta del modelo | 1 |" in markdown
    assert "Degradadas (el modelo falló; excluidas) | 1 |" in markdown
    assert "ADVERTENCIA: solo 1 de 3 preguntas" in markdown
    assert "evaluadas" not in markdown
    json.dumps(results)


def test_no_warning_when_most_questions_were_judged(db, questions) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    generator = ScriptedGenerator(["Respuesta [1].", "Respuesta [1].", "Respuesta [1]."])
    judge = Judge(JudgeLlm(), embed_query, sleep=lambda _s: None)
    results = evaluation.run_evaluation(db, questions, tiny_corpus(), ks=[1], judge=judge, generator=generator)
    assert results["judge"]["overall"]["n_judged"] == 3
    assert "ADVERTENCIA" not in render_markdown(results)


def test_a_rate_limited_judge_is_not_asked_again_by_the_judge_itself() -> None:
    from app.services.errors import LlmUnavailableError

    class Limited:
        model = "m"
        calls = 0

        def complete(self, _messages: list[dict[str, str]]) -> str:
            self.calls += 1
            raise LlmUnavailableError("limit", "rate_limited")

    limited = Limited()
    with pytest.raises(JudgeError):
        Judge(limited, lambda _t: None, sleep=lambda _s: None).context_recall("Una frase.", ["ctx"])
    assert limited.calls == 1  # the client already backed off; no 4x multiplication


def test_the_sweep_is_retrieval_only_and_marks_one_best_configuration(db, questions, monkeypatch) -> None:
    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)

    def no_llm(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("the sweep must not touch a language model")

    monkeypatch.setattr(llm, "get_client", no_llm)
    monkeypatch.setattr(llm, "get_judge_client", no_llm)
    sweep = evaluation.run_sweep(db, questions, tiny_corpus())
    configs = sweep["configs"]
    assert len(configs) == 1 + 2 * 4 and sweep["n_questions"] == 3
    assert (configs[0]["method"], configs[0]["dense_weight"]) == ("dense", None)
    assert [(c["method"], c["dense_weight"]) for c in configs[1:5]] == [("hybrid", w) for w in (1.0, 1.5, 2.0, 3.0)]
    assert {c["lexical_weight"] for c in configs[1:]} == {1.0}
    assert sum(c["best"] for c in configs) == 1
    best = next(c for c in configs if c["best"])
    top = max((c["overall"]["recall_chunk"][5] or 0, c["overall"]["mrr_chunk"] or 0) for c in configs)
    assert (best["overall"]["recall_chunk"][5] or 0, best["overall"]["mrr_chunk"] or 0) == top
    assert all(set(c["overall"]["recall_doc"]) == {1, 3, 5} for c in configs)
    markdown = render_sweep_markdown(sweep)
    assert "★" in markdown and "Peso denso" in markdown and "Recomendación" in markdown
    json.dumps(sweep)


def test_cli_sweep_writes_both_files_and_judge_flags_are_recorded(db, tmp_path, monkeypatch) -> None:
    import contextlib

    from scripts import rag_eval

    ml_client.set_client(FakeEmbedMl())
    load_tiny_corpus(db)
    corpus = tmp_path / "corpus"
    _write_corpus(corpus)
    questions_dir = tmp_path / "eval"
    questions_dir.mkdir()
    (questions_dir / "questions_general.yaml").write_text(QUESTIONS_GENERAL, encoding="utf-8")
    (questions_dir / "questions_triage.yaml").write_text(QUESTIONS_TRIAGE, encoding="utf-8")
    monkeypatch.setattr(rag_eval, "SessionLocal", lambda: contextlib.nullcontext(db))
    base = ["--corpus", str(corpus), "--questions", str(questions_dir), "--out", str(tmp_path / "out")]

    # --sweep: no model is ever requested, even with a model configured.
    llm.set_client(JudgeLlm())
    monkeypatch.setattr(llm, "get_client", lambda **_k: (_ for _ in ()).throw(AssertionError("LLM used")))
    assert rag_eval.main([*base, "--sweep"]) == 0
    sweep = json.loads((tmp_path / "out" / "sweep.json").read_text(encoding="utf-8"))
    assert len(sweep["configs"]) == 9 and (tmp_path / "out" / "SWEEP.md").exists()
    monkeypatch.undo()
    monkeypatch.setattr(rag_eval, "SessionLocal", lambda: contextlib.nullcontext(db))

    # --judge with run-only overrides, recorded in results.json and RESULTS.md.
    fake = JudgeLlm()
    llm.set_client(fake)
    llm.set_judge_client(fake)
    code = rag_eval.main(
        [*base, "--judge", "--method", "hybrid", "--context-k", "2",
         "--rrf-dense-weight", "2", "--rrf-lexical-weight", "0.5"]
    )
    assert code == 0
    saved = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert saved["run"]["rrf_dense_weight"] == 2.0 and saved["run"]["rrf_lexical_weight"] == 0.5
    assert saved["run"]["judge_method"] == "hybrid" and saved["run"]["context_k"] == 2
    assert saved["judge"]["k"] == 2 and saved["judge"]["method"] == "hybrid"
    markdown = (tmp_path / "out" / "RESULTS.md").read_text(encoding="utf-8")
    assert "método `hybrid`, k=2, pesos RRF denso=2.0 léxico=0.5" in markdown
    # The run-only overrides did not leak into the settings.
    from app.core.config import get_settings

    assert get_settings().rag_rrf_dense_weight == 1.0
