"""Reading the curated corpus: ``<corpus>/<base>/<ID>.md`` with YAML front matter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.models.enums import RagBase

CURATED_BASES = (RagBase.GENERAL.value, RagBase.TRIAGE.value)
REQUIRED_FIELDS = ("id", "base", "title", "publisher", "doc_type")
# Human-facing index files that live next to the documents but are not corpus content.
INDEX_FILES = frozenset({"SOURCES.md", "README.md"})


class CorpusFormatError(ValueError):
    """A corpus file that cannot be parsed or is missing required metadata."""


@dataclass(frozen=True)
class CorpusDocument:
    id: str
    base: str
    title: str
    publisher: str
    url: str | None
    year: int | None
    doc_type: str
    license: str | None
    language: str
    keywords: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


def parse_document(text: str, *, path: Path | None = None) -> CorpusDocument:
    where = str(path) if path else "document"
    if not text.startswith("---"):
        raise CorpusFormatError(f"{where}: front matter missing")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise CorpusFormatError(f"{where}: front matter is not closed")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise CorpusFormatError(f"{where}: invalid front matter ({exc})") from exc
    if not isinstance(meta, dict):
        raise CorpusFormatError(f"{where}: front matter must be a mapping")
    missing = [name for name in REQUIRED_FIELDS if not meta.get(name)]
    if missing:
        raise CorpusFormatError(f"{where}: missing fields {', '.join(missing)}")
    if meta["base"] not in CURATED_BASES:
        raise CorpusFormatError(f"{where}: base must be one of {', '.join(CURATED_BASES)}")
    keywords = meta.get("keywords") or []
    if not isinstance(keywords, list):
        raise CorpusFormatError(f"{where}: keywords must be a list")
    year = meta.get("year")
    return CorpusDocument(
        id=str(meta["id"]),
        base=str(meta["base"]),
        title=str(meta["title"]),
        publisher=str(meta["publisher"]),
        url=str(meta["url"]) if meta.get("url") else None,
        year=int(year) if year is not None else None,
        doc_type=str(meta["doc_type"]),
        license=str(meta["license"]) if meta.get("license") else None,
        language=str(meta.get("language") or "es"),
        keywords=[str(k) for k in keywords],
        body=parts[2].strip(),
        path=path,
    )


def load_corpus(root: Path) -> list[CorpusDocument]:
    """Every ``*.md`` under ``root/<base>/`` except index files; the folder must match the front matter."""

    documents: list[CorpusDocument] = []
    for base in CURATED_BASES:
        folder = root / base
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name in INDEX_FILES:
                continue
            document = parse_document(path.read_text(encoding="utf-8"), path=path)
            if document.base != base:
                raise CorpusFormatError(f"{path}: base '{document.base}' does not match its folder '{base}'")
            documents.append(document)
    ids = [d.id for d in documents]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise CorpusFormatError(f"Duplicated document ids: {', '.join(duplicated)}")
    return documents
