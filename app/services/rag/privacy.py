"""Pseudonymization of free text before it reaches a prompt (R12).

This is *pseudonymization*, not anonymization: the patient's name becomes the
stable pseudonym (``pz-...``), so answers can be linked back to the patient inside
the system, and direct identifiers become fixed tags. The text keeps clinical
meaning that could still identify a person in a very small population; that is why
only the minimum context is sent and every prompt is stored as evidence.

The sources of truth are the patient's own columns (name, document, phone, e-mail,
address), not only regular expressions: a regex cannot know a name.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models.patient import Patient

_DOCUMENT = re.compile(r"\bCC\s*\d[\d\.]{5,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?57[\s-]?)?3\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\w)")
# Standalone long integers can be documents. Preserve quantities, decimals,
# codes, dates, aliases and scientific notation; short clinical values never match.
_BARE_DOCUMENT = re.compile(r"(?<![\w./+\-])\d{6,10}(?![\w/\-]|[.,]\d)")
_CLINICAL_LABEL = re.compile(
    r"\b(?:glucosa|glucemia|fc|plaquetas|leucocitos|eritrocitos|carga\s+viral|"
    r"recuento|dosis|hematies|hematíes|snomed|loinc|codigo|código)\s*[:=]?\s*$", re.IGNORECASE,
)
_CLINICAL_UNIT = re.compile(
    r"\s*(?:(?:mg|g|kg|mcg|µg|ml|l|mmol|ui|iu|u|copias|celulas|células)\b|/|%)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[\w\.\-+]+@[\w\.\-]+\.\w+")
_NAME_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "san", "da"})
# A Colombian street address typed as free text ("Calle 10 # 5-20", "cra 7 no 45 - 12 sur").
# The "#" / "No" marker is required: "Cr 12" (creatinine) or "AV 2" must not match.
_ADDRESS = re.compile(
    r"\b(?:calle|carrera|cra|cr|kr|cl|avenida|av|diagonal|diag|transversal|trans|tv|circular|autopista)\b\.?"
    r"\s*\d+\s*[a-z]?\s*(?:bis\s*)?(?:(?:sur|norte|este|oeste)\s*)?"
    r"(?:#|n[uú]mero|no\.?|n[°º])\s*\d+\s*[a-z]?(?:\s*[-–]\s*\d+)?(?:\s*(?:sur|norte|este|oeste)\b)?",
    re.IGNORECASE,
)
_MIN_ADDRESS_SEGMENT = 5


@dataclass(frozen=True)
class PatientIdentity:
    pseudonym: str
    first_name: str
    last_name: str
    document_number: str
    phone: str | None
    email: str | None
    address: str | None

    @classmethod
    def of(cls, patient: Patient, pseudonym: str) -> PatientIdentity:
        return cls(
            pseudonym=pseudonym,
            first_name=patient.first_name or "",
            last_name=patient.last_name or "",
            document_number=patient.document_number or "",
            phone=patient.phone,
            email=patient.email,
            address=patient.address,
        )


def _fold(text: str) -> str:
    """Lowercase without accents, one output character per input character."""

    out: list[str] = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0]
        lowered = base.lower()
        out.append(lowered if len(lowered) == 1 else base)
    return "".join(out)


def _replace_word(text: str, needle: str, replacement: str) -> str:
    """Replace whole-word, case- and accent-insensitive occurrences of ``needle``."""

    needle = needle.strip()
    if not needle:
        return text
    pattern = re.compile(r"(?<!\w)" + re.escape(_fold(needle)) + r"(?!\w)")
    folded = _fold(text)
    pieces: list[str] = []
    last = 0
    for match in pattern.finditer(folded):
        pieces.append(text[last : match.start()])
        pieces.append(replacement)
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _digits_pattern(digits: str) -> re.Pattern[str]:
    """The same digits with optional dots, spaces or dashes between them."""

    return re.compile(r"(?<!\d)" + r"[\.\s\-]?".join(re.escape(d) for d in digits) + r"(?!\d)")


def _replace_tokens(text: str, phrase: str, replacement: str) -> str:
    """Replace ``phrase`` however its punctuation and spacing were typed.

    ``Calle 10 # 5-20`` also matches ``calle 10 #5 - 20`` and ``CALLE 10, 5 20``: the
    phrase is reduced to its word/number tokens, which must appear in order separated
    by anything that is not a letter or a digit.
    """

    tokens = re.findall(r"\w+", _fold(phrase))
    if not tokens or (len(tokens) == 1 and len(tokens[0]) < _MIN_ADDRESS_SEGMENT):
        return text
    pattern = re.compile(r"(?<!\w)" + r"[\W_]+".join(re.escape(t) for t in tokens) + r"(?!\w)")
    folded = _fold(text)
    pieces: list[str] = []
    last = 0
    for match in pattern.finditer(folded):
        pieces.append(text[last : match.start()])
        pieces.append(replacement)
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _scrub_address(text: str, address: str) -> str:
    """The whole address and each of its comma-separated parts that identify a place.

    The full text goes first; then a part is removed when it carries a digit or at least
    two words ("Calle 10 # 5-20", "Barrio Centro"), so a free-text mention of just the
    street does not survive. A lone short word ("Cali") is left: it is a city, not a home.
    """

    text = _replace_tokens(text, address, "[DIRECCION]")
    for part in re.split(r"[,;]", address):
        words = re.findall(r"\w+", part)
        if len(part.strip()) >= _MIN_ADDRESS_SEGMENT and (len(words) >= 2 or any(c.isdigit() for c in part)):
            text = _replace_tokens(text, part, "[DIRECCION]")
    return text


def _scrub_bare_documents(text: str) -> str:
    """Standalone 6 to 10 digit numbers become ``[DOCUMENTO]``, unless they read as clinical.

    A number after a lab label ("glucosa 180000") or before a unit ("150000 UI") is a
    measurement, not an identity document.
    """

    def replace_document(match: re.Match[str]) -> str:
        before, after = text[:match.start()], text[match.end():]
        if _CLINICAL_LABEL.search(before) or _CLINICAL_UNIT.match(after):
            return match.group()
        return "[DOCUMENTO]"

    return _BARE_DOCUMENT.sub(replace_document, text)


def scrub_generic(text: str) -> str:
    """Identity-less scrub for a question that is not about a known patient.

    Without a patient there is no name to look up, but a document, a mobile phone,
    an e-mail or a street address typed into the question must still not leave the system.
    """

    text = _DOCUMENT.sub("[DOCUMENTO]", text)
    text = _PHONE.sub("[TELEFONO]", text)
    text = _ADDRESS.sub("[DIRECCION]", text)
    text = _scrub_bare_documents(text)
    return _EMAIL.sub("[CORREO]", text)


def scrub(text: str, identity: PatientIdentity) -> str:
    """Remove the patient's direct identifiers from ``text``."""

    alias = identity.pseudonym
    first, last = identity.first_name.strip(), identity.last_name.strip()
    for full in (f"{first} {last}", f"{last} {first}", f"{last}, {first}"):
        text = _replace_word(text, full, alias)
    parts = {p for p in (*first.split(), *last.split()) if len(p) >= 3 and p.lower() not in _NAME_PARTICLES}
    for part in sorted(parts, key=len, reverse=True):
        text = _replace_word(text, part, alias)

    text = _DOCUMENT.sub("[DOCUMENTO]", text)
    document = re.sub(r"\D", "", identity.document_number)
    if 4 <= len(document) <= 12:
        text = _digits_pattern(document).sub("[DOCUMENTO]", text)
    text = _PHONE.sub("[TELEFONO]", text)
    phone = re.sub(r"\D", "", identity.phone or "")
    if len(phone) >= 7:
        text = _digits_pattern(phone).sub("[TELEFONO]", text)
    text = _EMAIL.sub("[CORREO]", text)
    if identity.email:
        text = _replace_word(text, identity.email, "[CORREO]")
    if identity.address:
        text = _scrub_address(text, identity.address)
    text = _ADDRESS.sub("[DIRECCION]", text)
    # A document typed without its "CC" prefix and not matching the stored one (a relative's
    # number, a mistyped digit) is still a direct identifier. Same guard as ``scrub_generic``.
    return _scrub_bare_documents(text)
