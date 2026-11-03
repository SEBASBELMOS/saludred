"""Evaluate the clinical RAG: retrieval metrics, abstention and (optionally) judge metrics.

    python -m scripts.rag_eval [--k 1,3,5] [--judge] [--chunking paragraph|fixed]

Reads rag/eval/questions_{general,triage}.yaml and the chunks already stored by
``scripts.rag_ingest`` (run it first, with the same ``--chunking``). Writes
rag/eval/results.json and rag/eval/RESULTS.md. With ``--judge`` it also needs the
Groq key: faithfulness, answer relevancy, context precision and context recall are
computed with an LLM judge following the RAGAS definitions (own implementation).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.database import SessionLocal
from app.services.rag import llm
from app.services.rag.chunking import STRATEGIES
from app.services.rag.corpus import CorpusFormatError, load_corpus
from app.services.rag.evaluation import EvaluationError, load_questions, run_evaluation, run_sweep
from app.services.rag.judge import Judge
from app.services.rag.report import render_markdown, render_sweep_markdown
from app.services.rag.retrieval import METHODS, embed_query


def _parse_ks(raw: str) -> list[int]:
    try:
        ks = sorted({int(part) for part in raw.split(",") if part.strip()})
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--k must be integers separated by commas, e.g. 1,3,5") from exc
    if not ks or ks[0] < 1:
        raise argparse.ArgumentTypeError("--k needs at least one positive integer")
    return ks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--k", type=_parse_ks, default=[1, 3, 5], help="cut-offs for Recall@k")
    parser.add_argument("--judge", action="store_true", help="RAGAS-style metrics with Groq as judge")
    parser.add_argument("--judge-model", default=None,
                        help="judge model id (default: RAG_JUDGE_MODEL, else the generator model)")
    parser.add_argument("--sleep", type=float, default=0.0, metavar="SECONDS",
                        help="pause after every LLM call (generation and judge)")
    parser.add_argument("--retries", type=int, default=5,
                        help="retries on HTTP 429 (retry-after, else 2/4/8/16/32 s)")
    parser.add_argument("--sweep", action="store_true",
                        help="retrieval-only comparison of RRF weights (no LLM calls) -> SWEEP.md, sweep.json")
    parser.add_argument("--method", choices=METHODS, default="hybrid_mmr",
                        help="retrieval method of the context given to the model in --judge runs")
    parser.add_argument("--context-k", type=int, default=None, metavar="N",
                        help="chunks given to the model in --judge runs (default RAG_DEFAULT_K); "
                             "--k is the list of Recall@k cut-offs")
    parser.add_argument("--rrf-dense-weight", type=float, default=None,
                        help="override RAG_RRF_DENSE_WEIGHT for this run only")
    parser.add_argument("--rrf-lexical-weight", type=float, default=None,
                        help="override RAG_RRF_LEXICAL_WEIGHT for this run only")
    parser.add_argument("--corpus", type=Path, default=Path("rag/corpus"))
    parser.add_argument("--questions", type=Path, default=Path("rag/eval"))
    parser.add_argument("--out", type=Path, default=Path("rag/eval"))
    parser.add_argument("--chunking", choices=STRATEGIES, default="paragraph",
                        help="strategy the chunks were ingested with (maps paragraphs to chunk ids)")
    parser.add_argument("--tag", default="", help="suffix for the output files, e.g. fixed -> results_fixed.json")
    args = parser.parse_args(argv)

    questions = load_questions(args.questions)
    if not questions:
        print(f"No questions found in {args.questions}/questions_*.yaml", file=sys.stderr)
        return 2
    try:
        documents = load_corpus(args.corpus)
    except CorpusFormatError as exc:
        print(f"Corpus error: {exc}", file=sys.stderr)
        return 2

    suffix = f"_{args.tag}" if args.tag else ""
    if args.sweep:
        # Retrieval only: no Groq client is even created on this path.
        with SessionLocal() as db:
            try:
                sweep = run_sweep(db, questions, documents, chunking=args.chunking, progress=print)
            except EvaluationError as exc:
                print(f"Sweep aborted: {exc}", file=sys.stderr)
                return 1
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"sweep{suffix}.json").write_text(
            json.dumps(sweep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.out / f"SWEEP{suffix}.md").write_text(render_sweep_markdown(sweep), encoding="utf-8")
        print(f"\nWrote {args.out}/sweep{suffix}.json and {args.out}/SWEEP{suffix}.md")
        return 0

    # Generation runs through the same throttle and 429 back-off as the judge; the
    # judge has its own client (and so its own per-model Groq quota).
    generator = llm.get_client(max_retries=args.retries)
    if generator is not None:
        generator = llm.ThrottledClient(generator, args.sleep)
    judge = None
    if args.judge:
        judge_client = llm.get_judge_client(model=args.judge_model, max_retries=args.retries)
        if generator is None or judge_client is None:
            print("--judge needs GROQ_API_KEY (the judge is an LLM).", file=sys.stderr)
            return 2
        judge = Judge(llm.ThrottledClient(judge_client, args.sleep), embed_query)

    with SessionLocal() as db:
        try:
            results = run_evaluation(
                db,
                questions,
                documents,
                ks=args.k,
                chunking=args.chunking,
                judge=judge,
                generator=generator,
                sleep_seconds=args.sleep,
                dense_weight=args.rrf_dense_weight,
                lexical_weight=args.rrf_lexical_weight,
                judge_method=args.method,
                context_k=args.context_k,
                progress=print,
            )
        except EvaluationError as exc:
            print(f"Evaluation aborted: {exc}", file=sys.stderr)
            return 1

    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"results{suffix}.json"
    md_path = args.out / f"RESULTS{suffix}.md"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(results), encoding="utf-8")
    print(f"\nWrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
