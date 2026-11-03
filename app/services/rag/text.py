"""Spanish text normalization shared by every lexical method.

Same semantics as the course notebook: lowercase, strip accents (NFD), keep
``[a-z0-9]+`` tokens (with an optional decimal part such as ``6,5``), drop
Spanish stopwords and one-letter tokens.
"""

from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    """a al algo con como de del el en entre es esta este esto la las le lo los mas o para pero por que
    se sin sobre su sus un una uno y ya cual cuales cuando donde debe deben puede ser son si no hay tiene
    tienen""".split()
)

_TOKEN = re.compile(r"[a-z0-9]+(?:,[0-9]+)?")


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def tokenize(text: str) -> list[str]:
    folded = strip_accents(text.lower())
    return [t for t in _TOKEN.findall(folded) if t not in STOPWORDS and len(t) > 1]
