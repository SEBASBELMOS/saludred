"""Illustrative tariffs, in integer Colombian pesos (COP).

The amounts are invented for the project: they are plausible, not an official
tariff manual (SOAT, ISS 2001 or an EPS-IPS contract). Replacing them with a
contracted tariff only means changing this module.
"""

from __future__ import annotations

import unicodedata

ANALISIS_IA = 45_000
CONSULTA = 85_000
REMISION = 60_000

# Price of one bed-day by the kind of service of the bed.
BED_DAY_HOSPITALIZACION = "hospitalizacion"
BED_DAY_UCI = "uci"
BED_DAY_URGENCIAS = "urgencias"
BED_DAY: dict[str, int] = {
    BED_DAY_HOSPITALIZACION: 380_000,
    BED_DAY_UCI: 1_850_000,
    BED_DAY_URGENCIAS: 260_000,
}


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def bed_day_category(service: str | None) -> str:
    """Tariff category of a bed from its service name ("UCI Adultos" -> ``uci``).

    Anything that is not recognizably an ICU or an emergency bed is billed as
    hospitalization, the ordinary ward day.
    """

    name = _plain(service or "")
    if "uci" in name or "intensiv" in name:
        return BED_DAY_UCI
    if "urgenc" in name:
        return BED_DAY_URGENCIAS
    return BED_DAY_HOSPITALIZACION


def bed_day_tariff(service: str | None) -> tuple[str, int]:
    """``(category, unit value)`` of one bed-day."""

    category = bed_day_category(service)
    return category, BED_DAY[category]
