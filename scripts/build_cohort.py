"""Build the real triage cohort and its cluster-based synthetic augmentation.

Reads MIMIC-IV-ED Demo v2.2 (``triage`` and ``edstays``), cleans it with rigid
clinical ranges, clusters it, generates synthetic rows inside each cluster and
stores everything in ``cohort_clusters`` / ``cohort_records``. The replacement
is one transaction, so running it twice leaves exactly one cohort.

Dataset: Johnson et al. (2023), MIMIC-IV-ED Demo v2.2, PhysioNet,
https://doi.org/10.13026/jzz5-vs76 (ODbL 1.0).

Usage (inside the API container, database taken from the settings):
    python -m scripts.build_cohort --download --summary-out /tmp/cohort_summary.json
    python -m scripts.build_cohort --triage triage.csv.gz --edstays edstays.csv.gz
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.core.database import SessionLocal
from app.services.cohort import persist_cohort
from app.services.cohort_builder import MULTIPLIER, SEED, build_cohort, read_table

BASE_URL = "https://physionet.org/files/mimic-iv-ed-demo/2.2/ed"
FILES = ("triage.csv.gz", "edstays.csv.gz")


def download(target: Path) -> tuple[Path, Path]:
    import httpx  # already a dependency of the API image

    paths = []
    for name in FILES:
        response = httpx.get(f"{BASE_URL}/{name}", follow_redirects=True, timeout=60)
        response.raise_for_status()
        path = target / name
        path.write_bytes(response.content)
        paths.append(path)
    return paths[0], paths[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--triage", type=Path, help="Ruta a triage.csv.gz")
    parser.add_argument("--edstays", type=Path, help="Ruta a edstays.csv.gz")
    parser.add_argument(
        "--download", action="store_true", help="Descargar los archivos desde PhysioNet"
    )
    parser.add_argument("--multiplier", type=int, default=MULTIPLIER)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--summary-out", type=Path, help="Ruta donde escribir el resumen JSON")
    args = parser.parse_args(argv)

    if not args.download and not (args.triage and args.edstays):
        parser.error("indique --download o bien --triage y --edstays")
    if args.multiplier < 1:
        parser.error("--multiplier debe ser >= 1")

    with tempfile.TemporaryDirectory() as tmp:
        if args.download:
            triage_path, stays_path = download(Path(tmp))
        else:
            triage_path, stays_path = args.triage, args.edstays
        result = build_cohort(
            read_table(triage_path),
            read_table(stays_path),
            multiplier=args.multiplier,
            seed=args.seed,
        )

    with SessionLocal() as db:
        persist_cohort(db, result)

    text = json.dumps(result.summary, indent=2, ensure_ascii=False)
    if args.summary_out:
        args.summary_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
