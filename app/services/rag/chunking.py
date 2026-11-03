"""Chunking strategies.

``paragraph`` (default): one chunk per paragraph of the source text. A paragraph
longer than ``MAX_WORDS`` is split at sentence boundaries, and a single sentence
longer than that falls back to fixed windows. A short paragraph is kept whole
(and reported) because merging it with its neighbour would blur two ideas.

``fixed``: the whole body is cut in windows of ``WINDOW`` words with ``OVERLAP``
words of overlap, ignoring paragraph borders. It exists so the evaluation can
show what the paragraph strategy buys.

Every chunk remembers the paragraphs it came from. With ``paragraph`` chunking
and paragraphs of at most ``MAX_WORDS`` words, chunk ``i`` is exactly paragraph
``i`` (the 1:1 assumption of the chunk-level metrics); otherwise the evaluation
uses ``paragraphs`` to know which chunks hold the expected paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_WORDS = 75
MIN_WORDS = 15
WINDOW = 60
OVERLAP = 15

STRATEGIES = ("paragraph", "fixed")

_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    paragraphs: tuple[int, ...]

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class ChunkedDocument:
    chunks: list[Chunk]
    # Indexes of paragraphs shorter than MIN_WORDS (kept, but worth a warning).
    short_paragraphs: list[int]
    paragraph_count: int


def split_paragraphs(body: str) -> list[str]:
    parts = (" ".join(p.split()) for p in re.split(r"\n\s*\n", body.strip()))
    return [p for p in parts if p]


def fixed_windows(words: list[str], window: int = WINDOW, overlap: int = OVERLAP) -> list[tuple[int, int]]:
    """``(start, end)`` word offsets of each window; the last one reaches the end."""

    step = max(1, window - overlap)
    spans: list[tuple[int, int]] = []
    for start in range(0, len(words), step):
        end = min(start + window, len(words))
        spans.append((start, end))
        if end == len(words):
            break
    return spans


def _split_long_paragraph(paragraph: str) -> list[str]:
    """Pack sentences into pieces of at most ``MAX_WORDS`` words."""

    pieces: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            pieces.append(" ".join(current))
            current, size = [], 0

    for sentence in _SENTENCE_END.split(paragraph):
        words = sentence.split()
        if len(words) > MAX_WORDS:
            flush()
            pieces.extend(" ".join(words[s:e]) for s, e in fixed_windows(words))
            continue
        if size + len(words) > MAX_WORDS:
            flush()
        current.append(sentence)
        size += len(words)
    flush()
    return pieces


def chunk_document(body: str, strategy: str = "paragraph") -> ChunkedDocument:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown chunking strategy: {strategy}")
    paragraphs = split_paragraphs(body)
    short = [i for i, p in enumerate(paragraphs) if len(p.split()) < MIN_WORDS]

    chunks: list[Chunk] = []
    if strategy == "paragraph":
        for p_index, paragraph in enumerate(paragraphs):
            pieces = [paragraph] if len(paragraph.split()) <= MAX_WORDS else _split_long_paragraph(paragraph)
            for piece in pieces:
                chunks.append(Chunk(index=len(chunks), text=piece, paragraphs=(p_index,)))
    else:
        words: list[str] = []
        owner: list[int] = []
        for p_index, paragraph in enumerate(paragraphs):
            tokens = paragraph.split()
            words.extend(tokens)
            owner.extend([p_index] * len(tokens))
        for start, end in fixed_windows(words):
            covered = tuple(sorted(set(owner[start:end])))
            chunks.append(Chunk(index=len(chunks), text=" ".join(words[start:end]), paragraphs=covered))
    return ChunkedDocument(chunks=chunks, short_paragraphs=short, paragraph_count=len(paragraphs))
