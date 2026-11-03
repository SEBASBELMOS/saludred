"""Load the curated RAG corpus into ``rag_chunks``.

    python -m scripts.rag_ingest [--corpus rag/corpus] [--reset] [--chunking paragraph|fixed]

Reads ``<corpus>/{general,triage}/<ID>.md``, chunks each document, embeds the
chunks through the ``ml`` service and upserts them. Running it twice changes
nothing. ``--reset`` first removes the guideline chunks (never the patients'
approved history).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.core.database import SessionLocal
from app.services.errors import MlServiceRejectedError, MlServiceUnavailableError
from app.services.rag.chunking import STRATEGIES
from app.services.rag.corpus import CorpusFormatError, load_corpus
from app.services.rag.ingest import ingest_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--corpus", type=Path, default=Path("rag/corpus"))
    parser.add_argument("--reset", action="store_true", help="remove guideline chunks first")
    parser.add_argument("--chunking", choices=STRATEGIES, default="paragraph")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="skip embeddings (lexical retrieval only); useful when ml is not running",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        documents = load_corpus(args.corpus)
    except CorpusFormatError as exc:
        print(f"Corpus error: {exc}", file=sys.stderr)
        return 2
    if not documents:
        print(f"No documents found under {args.corpus}/{{general,triage}}", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        try:
            summary = ingest_corpus(
                db,
                documents,
                chunking=args.chunking,
                reset=args.reset,
                embed=not args.no_embed,
                progress=print,
            )
        except (MlServiceUnavailableError, MlServiceRejectedError) as exc:
            db.rollback()
            print(f"Embedding service failed, nothing was written: {exc}", file=sys.stderr)
            return 1

    print(f"\nChunking: {args.chunking}  |  removed: {summary.removed}  |  embedded now: {summary.embedded_now}"
          f"  |  history embeddings backfilled: {summary.history_backfilled}")
    print(f"{'base':<10}{'docs':>6}{'chunks':>8}{'avg words':>11}{'embedded':>10}")
    for base, row in sorted(summary.per_base.items()):
        print(f"{base:<10}{row.documents:>6}{row.chunks:>8}{row.average_words:>11.1f}{row.embedded:>10}")
    if summary.warnings:
        print(f"\n{len(summary.warnings)} warning(s):")
        for warning in summary.warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
