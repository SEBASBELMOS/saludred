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
_PHONE = re.compile(r"\b(?:tel[eé]fono\s*)?3\d{9}\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w\.\-+]+@[\w\.\-]+\.\w+")
_NAME_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "san", "da"})


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


def scrub_generic(text: str) -> str:
    """Identity-less scrub for a question that is not about a known patient.

    Without a patient there is no name to look up, but a document, a mobile phone
    or an e-mail typed into the question must still not leave the system.
    """

    text = _DOCUMENT.sub("[DOCUMENTO]", text)
    text = _PHONE.sub("[TELEFONO]", text)
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
        text = _replace_word(text, identity.address, "[DIRECCION]")
    return text
