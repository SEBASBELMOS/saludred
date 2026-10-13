"""Export the de-identified training table for the criticality model.

Offline developer tool, not part of the image build. It reuses the cleaning of
the Epic 3 cohort (``app.services.cohort_builder.clean_rows``), so the 192 real
rows are the same ones the cohort endpoint reports, and joins two more public
MIMIC-IV tables to add the demographics the model needs:

* ``edstays``  -> sex (``gender``)
* ``patients`` -> ``anchor_age`` (MIMIC-IV Demo, hosp module)

Identifiers (``subject_id``, ``stay_id``, timestamps) never reach the output.
``group`` is an opaque index per patient, kept only so cross-validation can
keep all visits of one person in the same fold.

Run from the repository root:

    python -m ml.tools.export_triage --triage triage.csv.gz \
        --edstays edstays.csv.gz --patients patients.csv.gz

Sources: MIMIC-IV-ED Demo v2.2 (doi 10.13026/jzz5-vs76) and MIMIC-IV Clinical
Database Demo v2.2 (doi 10.13026/dp1f-ex47), both ODbL 1.0 on PhysioNet.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from app.services.cohort_builder import SEED, clean_rows, read_table

OUTPUT = Path(__file__).resolve().parents[1] / "data" / "criticality_triage.csv"
COLUMNS = [
    "group",
    "age",
    "sex",
    "temperature_c",
    "heart_rate",
    "resp_rate",
    "o2sat",
    "sbp",
    "dbp",
    "acuity",
    "admitted",
]


def export(triage: Path, edstays: Path, patients: Path, output: Path = OUTPUT) -> int:
    stays = read_table(edstays)
    kept, _drops = clean_rows(read_table(triage), stays, random.Random(SEED))

    stay_info = {
        row["stay_id"].strip(): (row["subject_id"].strip(), row["gender"].strip().upper())
        for row in stays
    }
    age_of = {
        row["subject_id"].strip(): int(row["anchor_age"]) for row in read_table(patients)
    }

    # Opaque, stable per-patient index (order of first appearance).
    group_of: dict[str, int] = {}
    rows: list[dict[str, object]] = []
    for item in kept:
        stay_id = item.source_ref.split("stay_id=")[1]
        subject, sex = stay_info[stay_id]
        if subject not in age_of or sex not in {"F", "M"}:
            continue
        group = group_of.setdefault(subject, len(group_of) + 1)
        rows.append(
            {
                "group": group,
                "age": age_of[subject],
                "sex": sex,
                "temperature_c": round(item.temperature_c, 1),
                "heart_rate": item.heart_rate,
                "resp_rate": item.resp_rate,
                "o2sat": item.o2sat,
                "sbp": item.sbp,
                "dbp": item.dbp,
                "acuity": item.acuity,
                "admitted": int(item.admitted),
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--triage", type=Path, required=True)
    parser.add_argument("--edstays", type=Path, required=True)
    parser.add_argument("--patients", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(f"{export(args.triage, args.edstays, args.patients, args.output)} rows -> {args.output}")


if __name__ == "__main__":
    main()
