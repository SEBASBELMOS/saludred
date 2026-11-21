"""Prompt-injection guard, layers 1 and 2 (slides 17 and 18: "El modelo cae. La arquitectura no.").

* Layer 1, ``wrap_untrusted``: every text that does not come from the system (the
  patient summary, guideline passages) reaches the model inside
  ``<datos_no_confiables>``; the system prompt says what that tag means. A closing
  (or opening) tag inside the content is neutralised so the text cannot leave it.
* Layer 2, ``inspect``: four regular expressions ported from the slide-17 demo. It
  is an ALARM, not a wall: a hit is logged and audited, it does not block the turn
  (a regex is trivial to evade; the real barriers are the tool policy in code and
  the human approval of clinical writes).

The other two layers live elsewhere: the patient comes from the session, never from
a model argument (``tools.py``/``context.py``), and a write waits for the clinician
(``harness.py``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

TAG = "datos_no_confiables"
_TAG_PIECE = re.compile(r"<\s*(/?)\s*" + TAG, re.IGNORECASE)

# The slide's four patterns. Matching runs on accent-folded, lower-cased text, so
# ``act[uú]a`` and ``env[ií]a`` need no character classes.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignorar_instrucciones",
     re.compile(r"ignor(a|ar|e)\s+(todas\s+)?(las\s+|tus\s+)?(instrucciones|reglas)")),
    ("cambiar_rol",
     re.compile(r"(ahora\s+eres|actua\s+como|eres\s+el)\s+(un\s+|una\s+)?(administrador|admin|sistema)")),
    ("ordenar_herramientas",
     re.compile(r"(llama|ejecuta|usa|invoca)\s+(a\s+)?(la\s+)?herramienta")),
    ("sacar_datos",
     re.compile(r"(envia|manda|reenvia|exporta|comparte).{0,60}(correo|email|https?://)")),
)

LOW, MEDIUM, HIGH = "bajo", "medio", "alto"


@dataclass(frozen=True)
class Detection:
    level: str  # bajo (nothing) | medio (one pattern) | alto (two or more)
    patterns: tuple[str, ...] = ()

    @property
    def detected(self) -> bool:
        return self.level != LOW


def _fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )


def scan(text: str) -> Detection:
    """Risk level of a text, as in the slide: 2+ patterns high, 1 medium, 0 low."""

    folded = _fold(text or "")
    hits = tuple(name for name, pattern in PATTERNS if pattern.search(folded))
    level = HIGH if len(hits) >= 2 else MEDIUM if hits else LOW
    return Detection(level, hits)


def neutralize(text: str) -> str:
    """Make any ``<datos_no_confiables>`` / ``</datos_no_confiables>`` in ``text`` inert."""

    return _TAG_PIECE.sub(lambda m: "&lt;" + m.group(1) + TAG, text)


def wrap_untrusted(source: str, text: str) -> str:
    """``text`` delimited as untrusted data, tagged with where it came from."""

    safe_source = re.sub(r"[^\w.-]", "", source)[:40]
    return f'<{TAG} fuente="{safe_source}">\n{neutralize(text)}\n</{TAG}>'
