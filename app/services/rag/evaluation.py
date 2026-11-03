"""Retrieval metrics for the evaluation set: Recall@k, MRR and abstention.

Doc level: a hit when a chunk of ``expected_doc`` is among the top ``k`` chunks.
Chunk level: a hit when a chunk that contains ``expected_paragraph`` of
``expected_doc`` is among them. With ``paragraph`` chunking and paragraphs of at
most 75 words, paragraph ``i`` is exactly chunk ``<doc>-<i>`` (1:1); a longer
paragraph was split into several chunks and ANY of them counts. With ``fixed``
chunking every chunk that overlaps the paragraph counts. ``expected_chunks`` below
computes that set from the same chunker the ingest uses.

MRR is computed on the ranking returned for the deepest ``k`` (the ranking is of
chunks, not of documents): the reciprocal rank of the first matching chunk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import RagAnswerMode
from app.services.rag import answer as rag_answer
from app.services.rag import store
from app.services.rag.chunking import chunk_document
from app.services.rag.corpus import CorpusDocument
from app.services.rag.judge import Judge, JudgeError
from app.services.rag.llm import LlmClient
from app.services.rag.prompt import ABSTENTION_TEXT
from app.services.rag.retrieval import METHODS, embed_query, retrieve
from app.services.rag.stats import EMBEDDING_MODEL
from app.services.rag.store import ChunkMeta

ABSTAIN = "ABSTAIN"
QUESTION_FILES = ("questions_general.yaml", "questions_triage.yaml")


@dataclass(frozen=True)
class Question:
    dataset: str
    id: str
    question: str
    expected_doc: str | None
    expected_paragraph: int | None
    reference_answer: str

    @property
    def is_abstention(self) -> bool:
        return self.reference_answer.strip().upper() == ABSTAIN or self.expected_doc is None


def load_questions(folder: Path) -> list[Question]:
    questions: list[Question] = []
    for name in QUESTION_FILES:
        path = folder / name
        if not path.exists():
            continue
        dataset = name.removeprefix("questions_").removesuffix(".yaml")
        for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            paragraph = item.get("expected_paragraph")
            questions.append(
                Question(
                    dataset=dataset,
                    id=str(item["id"]),
                    question=str(item["question"]),
                    expected_doc=item.get("expected_doc"),
                    expected_paragraph=int(paragraph) if paragraph is not None else None,
                    reference_answer=str(item.get("reference_answer") or ""),
                )
            )
    return questions


def expected_chunks(documents: list[CorpusDocument], chunking: str) -> dict[tuple[str, int], set[str]]:
    """``(doc_id, paragraph) -> chunk ids that contain that paragraph``."""

    mapping: dict[tuple[str, int], set[str]] = {}
    for document in documents:
        for chunk in chunk_document(document.body, chunking).chunks:
            for paragraph in chunk.paragraphs:
                mapping.setdefault((document.id, paragraph), set()).add(f"{document.id}-{chunk.index}")
    return mapping


def non_one_to_one_documents(documents: list[CorpusDocument]) -> list[str]:
    """Documents where paragraph chunking is NOT 1:1 (some paragraph was split)."""

    out = []
    for document in documents:
        result = chunk_document(document.body, "paragraph")
        if len(result.chunks) != result.paragraph_count:
            out.append(document.id)
    return out


def score_ranking(
    ranking: list[ChunkMeta], question: Question, targets: set[str] | None, ks: list[int]
) -> dict[str, Any]:
    """Hits at each ``k`` and reciprocal ranks for one question and one method."""

    docs = [c.doc_id for c in ranking]
    ids = [c.chunk_id for c in ranking]
    doc_rank = next((i for i, d in enumerate(docs, start=1) if d == question.expected_doc), None)
    chunk_rank = (
        next((i for i, c in enumerate(ids, start=1) if c in targets), None) if targets else None
    )
    return {
        "doc_hits": {k: doc_rank is not None and doc_rank <= k for k in ks},
        "chunk_hits": None if not targets else {k: chunk_rank is not None and chunk_rank <= k for k in ks},
        "rr_doc": 1.0 / doc_rank if doc_rank else 0.0,
        "rr_chunk": None if not targets else (1.0 / chunk_rank if chunk_rank else 0.0),
        "top": ids[: max(ks)],
    }


def aggregate(scores: list[dict[str, Any]], ks: list[int]) -> dict[str, Any]:
    """Means over the questions; chunk-level means only over those that have a target."""

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    chunk_scores = [s for s in scores if s["chunk_hits"] is not None]
    return {
        "n": len(scores),
        "n_chunk_level": len(chunk_scores),
        "recall_doc": {k: mean([float(s["doc_hits"][k]) for s in scores]) for k in ks},
        "recall_chunk": {k: mean([float(s["chunk_hits"][k]) for s in chunk_scores]) for k in ks},
        "mrr_doc": mean([s["rr_doc"] for s in scores]),
        "mrr_chunk": mean([s["rr_chunk"] for s in chunk_scores]),
    }


class EvaluationError(RuntimeError):
    """The evaluation cannot run (no embeddings, empty index, ...)."""


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


JUDGE_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
STATUS_JUDGED, STATUS_DEGRADED, STATUS_ABSTAINED = "judged", "degraded", "abstained"


def _judge_question(
    db: Session,
    judge: Judge,
    generator: LlmClient | None,
    question: Question,
    query_vector: list[float],
    k: int,
    method: str = "hybrid_mmr",
    weights: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Generate the production answer for one question and score it.

    ``status`` tells honestly what happened: ``judged`` (a model answered and was
    scored), ``degraded`` (the model failed, e.g. HTTP 429, and the answer was
    extractive: nothing to grade, excluded from the averages) or ``abstained``
    (the system declined; only the context metrics, which do not depend on the
    answer, are computed).
    """

    result = retrieve(
        db, question.question, method=method, k=k, query_vector=query_vector, **(weights or {})
    )
    evidence = result.has_evidence()
    sources = [rag_answer.source_from_hit(i, hit) for i, hit in enumerate(result.hits, start=1)] if evidence else []
    contexts = [src.text for src in sources]
    row: dict[str, Any] = {
        "id": question.id,
        "dataset": question.dataset,
        "answer": ABSTENTION_TEXT,
        "mode": RagAnswerMode.ABSTENTION.value,
        "status": STATUS_ABSTAINED,
        "degraded_reason": None,
        "contexts": [src.chunk_id for src in sources],
        **{name: None for name in JUDGE_METRICS},
    }
    if sources:
        composition = rag_answer.compose_answer(
            question.question, sources, degraded=result.degraded, client=generator
        )
        row.update(answer=composition.answer, mode=composition.mode.value, degraded_reason=composition.degraded_reason)
        if composition.mode == RagAnswerMode.LLM:
            row["status"] = STATUS_JUDGED
        elif composition.mode != RagAnswerMode.ABSTENTION:
            row["status"] = STATUS_DEGRADED
            return row
    if row["status"] == STATUS_ABSTAINED and not contexts:
        return row
    steps: dict[str, Callable[[], float | None]] = {
        "context_precision": lambda: judge.context_precision(
            question.question, question.reference_answer, contexts
        ),
        "context_recall": lambda: judge.context_recall(question.reference_answer, contexts),
    }
    if row["status"] == STATUS_JUDGED:
        steps = {
            "faithfulness": lambda: judge.faithfulness(row["answer"], contexts),
            "answer_relevancy": lambda: judge.answer_relevancy(question.question, row["answer"]),
            **steps,
        }
    for name, step in steps.items():
        try:
            row[name] = step()
        except JudgeError as exc:
            row.setdefault("errors", []).append(f"{name}: {exc}")
    return row


def summarize_judged(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Averages over what was really judged, with the counts that qualify them."""

    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    summary: dict[str, Any] = {
        "n_questions": len(rows),
        "n_judged": count(STATUS_JUDGED),
        "n_degraded": count(STATUS_DEGRADED),
        "n_abstained": count(STATUS_ABSTAINED),
    }
    for name in JUDGE_METRICS:
        values = [r[name] for r in rows if r[name] is not None]
        summary[name] = _mean(values)
        summary[f"n_{name}"] = len(values)
    return summary


def run_evaluation(
    db: Session,
    questions: list[Question],
    documents: list[CorpusDocument],
    *,
    ks: list[int],
    chunking: str = "paragraph",
    judge: Judge | None = None,
    generator: LlmClient | None = None,
    sleep_seconds: float = 0.0,
    dense_weight: float | None = None,
    lexical_weight: float | None = None,
    judge_method: str = "hybrid_mmr",
    context_k: int | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Retrieval metrics per method, abstention accuracy and (optionally) judge metrics."""

    settings = get_settings()
    index = store.get_index(db)
    if not index.chunks:
        raise EvaluationError("rag_chunks is empty: run `python -m scripts.rag_ingest` first")
    weights: dict[str, float | None] = {"dense_weight": dense_weight, "lexical_weight": lexical_weight}
    effective_dense = settings.rag_rrf_dense_weight if dense_weight is None else dense_weight
    effective_lexical = settings.rag_rrf_lexical_weight if lexical_weight is None else lexical_weight
    judge_k = context_k or settings.rag_default_k
    targets_by_paragraph = expected_chunks(documents, chunking)
    depth = max(max(ks), settings.rag_candidates)
    answerable = [q for q in questions if not q.is_abstention]
    abstain = [q for q in questions if q.is_abstention]

    per_method: dict[str, dict[str, list[dict[str, Any]]]] = {m: {} for m in METHODS}
    abstention_rows: list[dict[str, Any]] = []
    judge_rows: list[dict[str, Any]] = []
    vectors: dict[str, list[float]] = {}
    for position, question in enumerate(questions, start=1):
        vector = embed_query(question.question)
        if vector is None:
            raise EvaluationError("the embedding service (ml) is unavailable: dense metrics need it")
        vectors[question.id] = vector
        progress(f"[{position}/{len(questions)}] {question.id}")
        signals = None
        for method in METHODS:
            result = retrieve(
                db, question.question, method=method, k=depth, query_vector=vector, **weights
            )
            if method == "hybrid_mmr":
                signals = result
            if question.is_abstention:
                continue
            targets = (
                targets_by_paragraph.get((question.expected_doc or "", question.expected_paragraph))
                if question.expected_paragraph is not None
                else None
            )
            score = score_ranking([h.chunk for h in result.hits], question, targets, ks)
            score["id"] = question.id
            per_method[method].setdefault(question.dataset, []).append(score)
        assert signals is not None
        abstained = not signals.has_evidence(settings)
        abstention_rows.append(
            {
                "id": question.id,
                "dataset": question.dataset,
                "expected_abstain": question.is_abstention,
                "abstained": abstained,
                "max_cosine": round(signals.max_cosine or 0.0, 4),
                "max_bm25": round(signals.max_bm25, 4),
                "bm25_top_in_dense_top": signals.bm25_top_in_dense_top,
            }
        )

    retrieval = {}
    for method, by_dataset in per_method.items():
        everything = [row for rows in by_dataset.values() for row in rows]
        retrieval[method] = {"overall": aggregate(everything, ks)} | {
            dataset: aggregate(rows, ks) for dataset, rows in sorted(by_dataset.items())
        }

    expected_abstain = [r for r in abstention_rows if r["expected_abstain"]]
    expected_answer = [r for r in abstention_rows if not r["expected_abstain"]]
    abstention = {
        "thresholds": {"min_cosine": settings.rag_min_cosine, "min_bm25": settings.rag_min_bm25},
        "n_out_of_scope": len(expected_abstain),
        "correct_abstentions": sum(r["abstained"] for r in expected_abstain),
        "accuracy": _mean([float(r["abstained"]) for r in expected_abstain]),
        "n_answerable": len(expected_answer),
        "false_abstentions": sum(r["abstained"] for r in expected_answer),
        "false_abstention_rate": _mean([float(r["abstained"]) for r in expected_answer]),
        "details": abstention_rows,
    }

    judged = None
    if judge is not None:
        for position, question in enumerate(answerable, start=1):
            progress(f"judge [{position}/{len(answerable)}] {question.id}")
            judge_rows.append(
                _judge_question(
                    db, judge, generator, question, vectors[question.id], judge_k, judge_method, weights
                )
            )
        datasets = sorted({r["dataset"] for r in judge_rows})
        judged = {
            "model": judge.model,
            "k": judge_k,
            "method": judge_method,
            "n_answerable": len(answerable),
            "overall": summarize_judged(judge_rows),
            "by_dataset": {
                d: summarize_judged([r for r in judge_rows if r["dataset"] == d]) for d in datasets
            },
            "details": judge_rows,
        }

    counts = store.base_counts(db)
    docs_per_base: dict[str, int] = {}
    for document in documents:
        docs_per_base[document.base] = docs_per_base.get(document.base, 0) + 1
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunking": chunking,
        "ks": ks,
        "ranking_depth": depth,
        "models": {
            "embedding": EMBEDDING_MODEL,
            "llm": generator.model if generator is not None else None,
            "judge": judge.model if judge is not None else None,
        },
        "run": {
            "sleep_seconds": sleep_seconds,
            "rrf_dense_weight": effective_dense,
            "rrf_lexical_weight": effective_lexical,
            "judge_method": judge_method if judge is not None else None,
            "context_k": judge_k if judge is not None else None,
            "rate_limit_hits": sum(
                int(getattr(c, "rate_limit_hits", 0))
                for c in {id(x): x for x in (generator, judge.client if judge is not None else None) if x}.values()
            ),
        },
        "params": {
            "bm25": {"k1": 1.2, "b": 0.75},
            "rrf_k": 60,
            "mmr_lambda": settings.rag_mmr_lambda,
            "candidates": settings.rag_candidates,
            "judge_k": judge_k,
        },
        "corpus": {
            "documents": docs_per_base,
            "chunks": counts,
            "paragraph_chunking_not_one_to_one": non_one_to_one_documents(documents),
        },
        "questions": {
            "total": len(questions),
            "answerable": len(answerable),
            "out_of_scope": len(abstain),
        },
        "retrieval": retrieval,
        "abstention": abstention,
        "judge": judged,
    }


SWEEP_DENSE_WEIGHTS = (1.0, 1.5, 2.0, 3.0)
SWEEP_METHODS = ("hybrid", "hybrid_mmr")
SWEEP_KS = [1, 3, 5]


def run_sweep(
    db: Session,
    questions: list[Question],
    documents: list[CorpusDocument],
    *,
    chunking: str = "paragraph",
    dense_weights: tuple[float, ...] = SWEEP_DENSE_WEIGHTS,
    lexical_weight: float = 1.0,
    progress: Callable[[str], None] = lambda _msg: None,
) -> dict[str, Any]:
    """Retrieval-only comparison of RRF weightings. No language model is ever called.

    Configurations: dense alone (baseline), and each fused method at each dense
    weight (lexical weight fixed). The best one is chosen by chunk-level Recall@5,
    then chunk-level MRR.
    """

    settings = get_settings()
    if not store.get_index(db).chunks:
        raise EvaluationError("rag_chunks is empty: run `python -m scripts.rag_ingest` first")
    targets_by_paragraph = expected_chunks(documents, chunking)
    depth = max(max(SWEEP_KS), settings.rag_candidates)
    answerable = [q for q in questions if not q.is_abstention]
    configs: list[dict[str, Any]] = [{"method": "dense", "dense_weight": None, "lexical_weight": None}] + [
        {"method": m, "dense_weight": w, "lexical_weight": lexical_weight}
        for m in SWEEP_METHODS
        for w in dense_weights
    ]
    scores: list[list[dict[str, Any]]] = [[] for _ in configs]
    for position, question in enumerate(answerable, start=1):
        progress(f"sweep [{position}/{len(answerable)}] {question.id}")
        vector = embed_query(question.question)
        if vector is None:
            raise EvaluationError("the embedding service (ml) is unavailable: dense metrics need it")
        targets = (
            targets_by_paragraph.get((question.expected_doc or "", question.expected_paragraph))
            if question.expected_paragraph is not None
            else None
        )
        for i, config in enumerate(configs):
            weights = (
                {}
                if config["dense_weight"] is None
                else {"dense_weight": config["dense_weight"], "lexical_weight": config["lexical_weight"]}
            )
            result = retrieve(
                db, question.question, method=config["method"], k=depth, query_vector=vector, **weights
            )
            scores[i].append(score_ranking([h.chunk for h in result.hits], question, targets, SWEEP_KS))
    rows = []
    for config, config_scores in zip(configs, scores, strict=True):
        rows.append({**config, "overall": aggregate(config_scores, SWEEP_KS)})

    def key(row: dict[str, Any]) -> tuple[float, float]:
        agg = row["overall"]
        return (agg["recall_chunk"][5] or 0.0, agg["mrr_chunk"] or 0.0)

    best = max(range(len(rows)), key=lambda i: (key(rows[i]), -i))
    for i, row in enumerate(rows):
        row["best"] = i == best
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunking": chunking,
        "ks": SWEEP_KS,
        "ranking_depth": depth,
        "n_questions": len(answerable),
        "models": {"embedding": EMBEDDING_MODEL, "llm": None, "judge": None},
        "selection": "chunk-level Recall@5, then chunk-level MRR",
        "configs": rows,
    }
