"""Load real adult chest X-rays into the PACS through the API.

Source: NIH ChestX-ray14 (NIH Clinical Center, https://nihcc.app.box.com/v/ChestXray-NIHCC),
Hugging Face mirror ``chehablab/NIHChestXR``, shard 0 (parquet, 224x224 PNG).
Citation: Wang et al. (2017), ChestX-Ray8, IEEE CVPR, doi:10.1109/cvpr.2017.369.
Labels come from NLP over radiology reports: they are not confirmed diagnoses.

The script runs outside the API process (it needs ``pyarrow``, which the API
image does not ship). It only *reads* the database to choose which imaging
studies still have no pixels; every image goes in through the real endpoint
``POST /api/v1/imaging-studies/{id}/images``, so the PACS rules (identity
rewrite, validation, audit trail) apply exactly as for any other upload.

Usage:
    export SALUDRED_ADMIN_PASSWORD=...        # never passed on the command line
    python -m scripts.load_chest_xrays --download /tmp/nih --api http://localhost:8000 \\
        --manifest-out /tmp/xray_manifest.csv
    python -m scripts.load_chest_xrays --parquet shard0.parquet --dry-run \\
        --manifest-out /tmp/xray_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PARQUET_URL = (
    "https://huggingface.co/datasets/chehablab/NIHChestXR/resolve/main/data/"
    "train-00000-of-00006.parquet"
)
PARQUET_NAME = "train-00000-of-00006.parquet"

NORMAL_LABEL = "No Finding"
CRITICAL_LABELS = frozenset({"Pneumonia", "Effusion", "Edema", "Consolidation", "Cardiomegaly"})
MIN_AGE = 18

# Which patients receive which kind of image. It is a preference, not a rule:
# when a group runs out, the other studies fill in.
CRITICAL_PROFILES = ("RESPIRATORY", "CARDIAC", "ELDERLY_FRAIL")
NORMAL_PROFILES = ("HEALTHY", "HYPERTENSIVE")

MANIFEST_COLUMNS = [
    "study_id", "patient_id", "nih_scan_id", "labels", "age", "sex", "http_status",
]


class LabelMetadataError(RuntimeError):
    """The parquet does not say which class each label index stands for."""


@dataclass(frozen=True)
class Candidate:
    """A chest X-ray row, without its pixels."""

    row: int
    scan_id: str
    age: float | None
    sex: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Study:
    study_id: str
    patient_id: str
    profile: str | None


@dataclass
class Assignment:
    study: Study
    candidate: Candidate
    group: str  # "critical" or "normal"
    http_status: str = ""


@dataclass
class Selection:
    critical: list[Candidate] = field(default_factory=list)
    normal: list[Candidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure logic (no pyarrow, no database, no network)
# ---------------------------------------------------------------------------


def label_names_from_metadata(metadata: Mapping[Any, Any] | None) -> list[str]:
    """Class names of the ``labels`` column from the Hugging Face schema metadata.

    ``datasets`` stores the features as JSON under the ``huggingface`` key of the
    parquet schema. Nothing is guessed: if the names are not there, fail and
    ask for them (``--label-names``).
    """

    if not metadata:
        raise LabelMetadataError("El parquet no trae metadata del esquema")
    raw = metadata.get(b"huggingface", metadata.get("huggingface"))
    if raw is None:
        raise LabelMetadataError("La metadata no incluye la clave 'huggingface'")
    try:
        info = json.loads(raw)
        feature = info["info"]["features"]["labels"]
    except (KeyError, TypeError, ValueError) as exc:
        raise LabelMetadataError("La metadata no describe la columna 'labels'") from exc

    # Sequence(ClassLabel) nests the names one level down; a bare ClassLabel does not.
    inner = feature.get("feature", feature) if isinstance(feature, dict) else {}
    if isinstance(inner, list):
        inner = inner[0] if inner else {}
    names = inner.get("names") if isinstance(inner, dict) else None
    if not names:
        raise LabelMetadataError("La columna 'labels' no declara los nombres de clase")
    return [str(name) for name in names]


def names_from_option(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def to_candidates(rows: Iterable[Mapping[str, Any]], names: list[str]) -> list[Candidate]:
    """Attach readable label names to raw rows. Unknown indices are a hard error."""

    result = []
    for index, row in enumerate(rows):
        try:
            labels = tuple(names[int(i)] for i in (row.get("labels") or []))
        except IndexError as exc:
            raise LabelMetadataError(
                f"La fila {index} usa un indice de etiqueta fuera de la lista ({len(names)} nombres)"
            ) from exc
        age = row.get("age")
        result.append(
            Candidate(
                row=index,
                scan_id=str(row.get("scan_id", index)),
                age=float(age) if age is not None else None,
                sex=str(row.get("sex", "")),
                labels=labels,
            )
        )
    return result


def is_critical(candidate: Candidate) -> bool:
    return any(label in CRITICAL_LABELS for label in candidate.labels)


def is_normal(candidate: Candidate) -> bool:
    return set(candidate.labels) == {NORMAL_LABEL}


def select_images(
    candidates: Iterable[Candidate], n_critical: int, n_normal: int, seed: int
) -> Selection:
    """Seeded random sample of adult images: critical findings vs "No Finding" only.

    A "critical" image may carry other labels too (Pneumonia plus Infiltration);
    a "normal" one carries nothing else. Rows are sorted by index first so the
    result depends on the seed alone, not on the order they arrived in.
    """

    adults = sorted(
        (c for c in candidates if c.age is not None and c.age >= MIN_AGE), key=lambda c: c.row
    )
    critical_pool = [c for c in adults if is_critical(c)]
    normal_pool = [c for c in adults if is_normal(c)]
    rng = random.Random(seed)
    return Selection(
        critical=rng.sample(critical_pool, min(n_critical, len(critical_pool))),
        normal=rng.sample(normal_pool, min(n_normal, len(normal_pool))),
    )


def assign_studies(
    selection: Selection, studies: Iterable[Study], seed: int
) -> list[Assignment]:
    """Pair each image with its own study: never two images in one study.

    Critical images go first to respiratory, cardiac and frail patients, normal
    ones to healthy and hypertensive ones. Whatever is left over fills the gaps.
    """

    rng = random.Random(seed)
    ordered = sorted(studies, key=lambda s: s.study_id)
    rng.shuffle(ordered)
    for_critical = [s for s in ordered if s.profile in CRITICAL_PROFILES]
    for_normal = [s for s in ordered if s.profile in NORMAL_PROFILES]
    neutral = [s for s in ordered if s.profile not in CRITICAL_PROFILES + NORMAL_PROFILES]

    used: set[str] = set()

    def take(pools: list[list[Study]]) -> Study | None:
        for pool in pools:
            for study in pool:
                if study.study_id not in used:
                    used.add(study.study_id)
                    return study
        return None

    assignments: list[Assignment] = []
    for candidate in selection.critical:
        study = take([for_critical, neutral, for_normal])
        if study:
            assignments.append(Assignment(study, candidate, "critical"))
    for candidate in selection.normal:
        study = take([for_normal, neutral, for_critical])
        if study:
            assignments.append(Assignment(study, candidate, "normal"))
    return assignments


def write_manifest(path: Path, assignments: Iterable[Assignment]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MANIFEST_COLUMNS)
        for a in assignments:
            c = a.candidate
            writer.writerow(
                [
                    a.study.study_id, a.study.patient_id, c.scan_id, "|".join(c.labels),
                    "" if c.age is None else int(c.age), c.sex, a.http_status,
                ]
            )


def content_type_of(data: bytes) -> str:
    """Declared type for the upload; the API decides by signature anyway."""

    return "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"


# ---------------------------------------------------------------------------
# I/O (pyarrow, database, HTTP): imported lazily so tests need none of it
# ---------------------------------------------------------------------------


def download_parquet(target_dir: Path) -> Path:
    import httpx

    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / PARQUET_NAME
    if path.exists():
        return path
    part = path.with_suffix(".part")
    with httpx.stream("GET", PARQUET_URL, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with open(part, "wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    part.rename(path)
    return path


def read_candidates(parquet: Path, label_option: str | None) -> tuple[Any, list[Candidate]]:
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(parquet)
    if label_option:
        names = names_from_option(label_option)
    else:
        try:
            names = label_names_from_metadata(handle.schema_arrow.metadata)
        except LabelMetadataError as exc:
            raise LabelMetadataError(
                f"{exc}. Indique la lista con --label-names 'Atelectasis,Cardiomegaly,...' "
                "en el orden de los indices."
            ) from exc
    if NORMAL_LABEL not in names:
        raise LabelMetadataError(f"La lista de etiquetas no incluye '{NORMAL_LABEL}': {names}")
    columns = [c for c in ("scan_id", "age", "sex", "labels") if c in handle.schema_arrow.names]
    rows = handle.read(columns=columns).to_pylist()
    return handle, to_candidates(rows, names)


def load_pixels(handle: Any, wanted: set[int]) -> dict[int, bytes]:
    """Image bytes of the wanted rows only, streaming batches to keep memory low."""

    found: dict[int, bytes] = {}
    offset = 0
    for batch in handle.iter_batches(columns=["image"], batch_size=256):
        for i, value in enumerate(batch.column("image").to_pylist()):
            if offset + i in wanted:
                found[offset + i] = value["bytes"]
        offset += batch.num_rows
    return found


def fetch_target_studies(database_url: str) -> list[Study]:
    """Studies that accept an X-ray: CR/DX, live, no pixels in the PACS yet. Read-only."""

    from sqlalchemy import create_engine, select, text

    from app.models import ImagingStudy, Patient
    from app.models.enums import ImagingModality

    engine = create_engine(database_url)
    query = (
        select(ImagingStudy.id, ImagingStudy.patient_id, Patient.clinical_profile)
        .join(Patient, Patient.id == ImagingStudy.patient_id)
        .where(
            ImagingStudy.modality.in_([ImagingModality.CR, ImagingModality.DX]),
            ImagingStudy.deleted_at.is_(None),
            ImagingStudy.pacs_study_id.is_(None),
            Patient.deleted_at.is_(None),
        )
    )
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SET TRANSACTION READ ONLY"))
        rows = connection.execute(query).all()
    engine.dispose()
    return [
        Study(str(sid), str(pid), profile.value if hasattr(profile, "value") else profile)
        for sid, pid, profile in rows
    ]


def upload_all(
    api: str, user: str, password: str, assignments: list[Assignment], pixels: dict[int, bytes]
) -> None:
    import httpx

    base = api.rstrip("/")
    with httpx.Client(base_url=base, timeout=60) as client:
        login = client.post("/api/v1/auth/login", json={"username": user, "password": password})
        if login.status_code != 200:
            raise SystemExit(f"No se pudo iniciar sesion (HTTP {login.status_code})")
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        for a in assignments:
            data = pixels.get(a.candidate.row)
            if not data:
                a.http_status = "sin_imagen"
                continue
            try:
                response = client.post(
                    f"/api/v1/imaging-studies/{a.study.study_id}/images",
                    content=data,
                    headers={**headers, "Content-Type": content_type_of(data)},
                )
                a.http_status = str(response.status_code)
            except httpx.HTTPError as exc:
                a.http_status = f"error:{exc.__class__.__name__}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--parquet", type=Path, help="Shard parquet local")
    source.add_argument("--download", type=Path, metavar="DIR", help="Descargar el shard 0 en DIR")
    parser.add_argument("--api", default="http://localhost:8000", help="URL base de la API")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--n-critical", type=int, default=30)
    parser.add_argument("--n-normal", type=int, default=30)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--label-names", help="Nombres de etiqueta separados por coma, en orden de indice")
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Elegir y escribir el manifiesto sin subir")
    args = parser.parse_args(argv)

    password = os.environ.get("SALUDRED_ADMIN_PASSWORD", "")
    if not args.dry_run and not password:
        parser.error("defina la variable de entorno SALUDRED_ADMIN_PASSWORD")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("defina la variable de entorno DATABASE_URL (solo lectura)")

    parquet = args.parquet or download_parquet(args.download)
    handle, candidates = read_candidates(parquet, args.label_names)
    selection = select_images(candidates, args.n_critical, args.n_normal, args.seed)
    studies = fetch_target_studies(database_url)
    assignments = assign_studies(selection, studies, args.seed)

    if len(assignments) < len(selection.critical) + len(selection.normal):
        print(
            f"Aviso: {len(studies)} estudios CR/DX sin imagenes para "
            f"{len(selection.critical) + len(selection.normal)} radiografias; "
            f"se cargaran {len(assignments)}.",
            file=sys.stderr,
        )

    if args.dry_run:
        for a in assignments:
            a.http_status = "dry-run"
    else:
        pixels = load_pixels(handle, {a.candidate.row for a in assignments})
        upload_all(args.api, args.user, password, assignments, pixels)

    write_manifest(args.manifest_out, assignments)
    statuses: dict[str, int] = {}
    for a in assignments:
        statuses[a.http_status] = statuses.get(a.http_status, 0) + 1
    print(
        json.dumps(
            {
                "candidates_adult": sum(1 for c in candidates if c.age is not None and c.age >= MIN_AGE),
                "selected_critical": len(selection.critical),
                "selected_normal": len(selection.normal),
                "target_studies": len(studies),
                "assigned": len(assignments),
                "http_status": statuses,
                "manifest": str(args.manifest_out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
