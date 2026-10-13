"""Generate the synthetic bed-cleaning events used by the cleaning-delay model.

One row = one bed released (discharge, transfer or end of isolation) that must
be cleaned before it can receive the next patient. There is no public dataset of
bed cleaning in Colombia, so the events are simulated; the parameters come from
the cleaning-time literature (CDC environmental cleaning, AHE benchmarks of
40-45 minutes from discharge to a ready bed) and anything without a source is
marked as a team assumption in ``PARAMETERS``.

The generator is the one used in the supervised-learning assignment (seed 2026)
with English column names and values; the draw order is unchanged, so the
numbers are the same rows.

Usage (from ``ml/``):
    python tools/generate_cleaning_events.py            # 800 events
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

SEED = 2026
# Critical delay: twice the 40-45 minute standard from discharge to ready bed.
CRITICAL_MINUTES = 90
# ATP bioluminescence pass mark (RLU), most common in the reviewed literature.
ATP_PASS_RLU = 250

NETWORK = {
    "Clinica Norte": ["Hospitalizacion", "UCI Adultos"],
    "Hospital Sur": ["Hospitalizacion", "Urgencias"],
    "Centro Medico Central": ["Hospitalizacion", "UCI Adultos"],
}
REASON_NAMES = {"alta": "discharge", "traslado": "transfer", "fin_aislamiento": "isolation_end"}
ISOLATION_NAMES = {
    "ninguno": "none",
    "contacto": "contact",
    "gotas": "droplet",
    "esporas_c_difficile": "spores",
}
SHIFT_NAMES = {"manana": "morning", "tarde": "afternoon", "noche": "night"}

PARAMETERS = {
    "base_cleaning_min": {"Hospitalizacion": 42, "UCI Adultos": 52, "Urgencias": 32},
    "isolation_extra_min": {"ninguno": 0, "contacto": 10, "gotas": 10, "esporas_c_difficile": 22},
    "curtain_extra_min": 8,
    "dirty_curtain_prob": 0.15,  # team assumption
    "isolation_mix": {  # team assumption
        "UCI Adultos": {"ninguno": 0.60, "contacto": 0.25, "gotas": 0.08, "esporas_c_difficile": 0.07},
        "Hospitalizacion": {"ninguno": 0.80, "contacto": 0.12, "gotas": 0.05, "esporas_c_difficile": 0.03},
        "Urgencias": {"ninguno": 0.85, "contacto": 0.10, "gotas": 0.04, "esporas_c_difficile": 0.01},
    },
    "median_stay_h": {"UCI Adultos": 120, "Hospitalizacion": 70, "Urgencias": 18},
    "staff_per_shift": {"manana": 4, "tarde": 3, "noche": 2},  # team assumption
    "verification_min": 5,
}

RANGES = {
    "wait_min": (0, 150),
    "cleaning_min": (20, 120),
    "atp_rlu": (20, 1500),
    "minutes_to_ready": (20, 300),
    "stay_hours": (2, 720),
}

COLUMNS = [
    "event_id",
    "ips",
    "service",
    "release_reason",
    "weekday",
    "release_hour",
    "shift",
    "isolation",
    "previous_stay_hours",
    "curtains_change_required",
    "cleaning_staff_on_shift",
    "pending_cleaning_beds",
    "service_occupancy",
    "patient_waiting",
    "minutes_to_ready",
    "late",
]


def shift_of(hour: int) -> str:
    if 6 <= hour < 14:
        return "manana"
    if 14 <= hour < 22:
        return "tarde"
    return "noche"


def _release_hour(rng: np.random.Generator, reason: str) -> int:
    if reason == "alta":
        return int(np.clip(round(rng.normal(13, 3)), 0, 23))
    return int(rng.integers(0, 24))


def generate(n: int, seed: int = SEED) -> list[dict[str, object]]:
    rng = np.random.default_rng(seed)
    p = PARAMETERS
    rows = []
    for i in range(n):
        ips = rng.choice(list(NETWORK))
        service = rng.choice(NETWORK[ips])
        reason = rng.choice(["alta", "traslado", "fin_aislamiento"], p=[0.75, 0.20, 0.05])
        hour = _release_hour(rng, reason)
        shift = shift_of(hour)
        mix = p["isolation_mix"][service]
        isolation = rng.choice(list(mix), p=list(mix.values()))
        if reason == "fin_aislamiento" and isolation == "ninguno":
            isolation = "contacto"
        stay = float(
            np.clip(rng.lognormal(np.log(p["median_stay_h"][service]), 0.6), *RANGES["stay_hours"])
        )
        curtains = isolation != "ninguno" or rng.random() < p["dirty_curtain_prob"]
        staff = int(max(1, p["staff_per_shift"][shift] + rng.integers(-1, 2)))
        lam = 1.8 if 10 <= hour <= 16 else 0.7
        pending = int(rng.poisson(lam))
        occupancy = float(np.clip(rng.beta(17, 3), 0.55, 1.0))
        waiting = bool(rng.random() < np.clip(0.2 + 1.5 * (occupancy - 0.8), 0.05, 0.9))

        # What happens after the release (never an input feature).
        load = (pending + 1) / staff
        wait = rng.gamma(2.0, 2.5 + 4.5 * load)
        if shift == "noche":
            wait += 6
        if waiting:
            wait *= 0.7
        wait = float(np.clip(wait, *RANGES["wait_min"]))

        cleaning = (
            p["base_cleaning_min"][service]
            + p["isolation_extra_min"][isolation]
            + (p["curtain_extra_min"] if curtains else 0)
            + min(stay / 48, 6)
            + rng.normal(0, 6)
        )
        cleaning = float(np.clip(cleaning, *RANGES["cleaning_min"]))

        rush = 1 + 0.35 * waiting + 0.25 * (shift == "noche") + 0.15 * min(load, 3)
        atp = float(np.clip(rng.lognormal(np.log(120 * rush), 0.55), *RANGES["atp_rlu"]))
        passed = atp <= ATP_PASS_RLU
        verification = p["verification_min"] + (0 if passed else 0.5 * cleaning)

        ready = float(np.clip(wait + cleaning + verification, *RANGES["minutes_to_ready"]))
        rows.append(
            {
                "event_id": i + 1,
                "ips": str(ips),
                "service": str(service),
                "release_reason": REASON_NAMES[str(reason)],
                "weekday": int(rng.integers(1, 8)),
                "release_hour": hour,
                "shift": SHIFT_NAMES[shift],
                "isolation": ISOLATION_NAMES[str(isolation)],
                "previous_stay_hours": round(stay, 1),
                "curtains_change_required": int(curtains),
                "cleaning_staff_on_shift": staff,
                "pending_cleaning_beds": pending,
                "service_occupancy": round(occupancy, 3),
                "patient_waiting": int(waiting),
                "minutes_to_ready": round(ready, 1),
                "late": int(ready > CRITICAL_MINUTES),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=800)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "cleaning_events.csv"
    )
    args = parser.parse_args()
    rows = generate(args.n)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    late = sum(r["late"] for r in rows) / len(rows)
    print(f"{len(rows)} events -> {args.output} (late: {late:.1%})")


if __name__ == "__main__":
    main()
