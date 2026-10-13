"""Triage cohort: cleaning, clustering, augmentation within clusters, storage, endpoint.

The CSV fixtures below are fabricated by the test with a fixed seed. They only
mimic the column layout of MIMIC-IV-ED Demo; none of it is the real data.
"""

from __future__ import annotations

import csv
import gzip
import random
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import CohortCluster, CohortRecord
from app.models.enums import CohortOrigin
from app.services import cohort as cohort_service
from app.services.cohort_builder import (
    FEATURES,
    RANGES,
    build_cohort,
    clean_rows,
    fahrenheit_to_celsius,
    parse_pain,
    read_table,
)

# Reusa la base de pruebas de las reglas de camas (dataset sintetico en SQLite).
from tests.test_bed_rules import client_as, db  # noqa: F401

TRIAGE_COLUMNS = [
    "subject_id", "stay_id", "temperature", "heartrate", "resprate",
    "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint",
]
STAY_COLUMNS = ["subject_id", "hadm_id", "stay_id", "gender", "disposition"]

# Three latent groups, mimicking stable / borderline / critical arrivals:
# (temp F, hr, rr, o2, sbp, dbp, acuity, admitted out of 20)
GROUPS = [
    (98.2, 78, 16, 98.5, 128, 78, 4, 3),
    (99.4, 92, 18, 96.0, 136, 82, 3, 10),
    (100.6, 116, 24, 91.0, 108, 64, 2, 18),
]


def _fixture_rows() -> tuple[list[dict], list[dict], dict[str, int]]:
    """~60 plausible rows plus one row for each rejection reason."""

    rng = random.Random(7)
    triage, stays = [], []
    stay_id = 1000

    def add(values, disposition="HOME", pain="5", subject=None):
        nonlocal stay_id
        stay_id += 1
        subject = subject or stay_id
        temp, hr, rr, o2, sbp, dbp, acuity = values
        triage.append(
            {
                "subject_id": str(subject), "stay_id": str(stay_id),
                "temperature": "" if temp is None else str(temp),
                "heartrate": str(hr), "resprate": str(rr), "o2sat": str(o2),
                "sbp": str(sbp), "dbp": str(dbp), "pain": pain,
                "acuity": str(acuity), "chiefcomplaint": "test",
            }
        )
        stays.append(
            {"subject_id": str(subject), "hadm_id": "", "stay_id": str(stay_id),
             "gender": "F", "disposition": disposition}
        )

    for temp, hr, rr, o2, sbp, dbp, acuity, admitted in GROUPS:
        for i in range(20):
            values = (
                round(temp + rng.uniform(-0.6, 0.6), 1),
                hr + rng.randint(-6, 6),
                rr + rng.randint(-2, 2),
                min(100, round(o2 + rng.uniform(-1.5, 1.5))),
                sbp + rng.randint(-8, 8),
                dbp + rng.randint(-5, 5),
                acuity,
            )
            add(values, "ADMITTED" if i < admitted else "HOME", pain=rng.choice(["3", "7", "unable", ""]))

    good = (98.6, 80, 16, 98, 120, 80, 3)
    add((None, 80, 16, 98, 120, 80, 3))                    # missing temperature
    add((98.6, 300, 16, 98, 120, 80, 3))                   # heart rate out of range
    add((98.6, 80, 16, 98, 70, 80, 3))                     # sbp below dbp
    add((98.6, 80, 16, 98, 120, 80, 9))                    # acuity out of range
    add((60.0, 80, 16, 98, 120, 80, 3))                    # 15.6 C: below rigid range
    triage.append({**triage[0], "subject_id": "999", "stay_id": "999999"})  # no stay
    add(good)
    triage.append(dict(triage[-1]))                        # same stay twice
    expected = {
        "missing_temperature_c": 1,
        "out_of_range_heart_rate": 1,
        "sbp_not_above_dbp": 1,
        "out_of_range_acuity": 1,
        "out_of_range_temperature_c": 1,
        "no_matching_stay": 1,
        "duplicate_stay": 1,
    }
    return triage, stays, expected


def _write_gz(path: Path, columns: list[str], rows: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture(scope="module")
def built():
    triage, stays, _ = _fixture_rows()
    return build_cohort(triage, stays)


# -- Parsing and cleaning ----------------------------------------------------


def test_fahrenheit_is_converted_to_celsius() -> None:
    assert fahrenheit_to_celsius(98.6) == 37.0
    assert fahrenheit_to_celsius(100.4) == 38.0
    assert fahrenheit_to_celsius(32.0) == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("7", 7), ("7.0", 7), ("0", 0), (" 10 ", 10), ("unable", None), ("", None),
     ("11", None), ("-1", None), (None, None), ("nan", None)],
)
def test_pain_parsing(raw, expected) -> None:
    assert parse_pain(raw) == expected


def test_cleaning_counts_every_drop_reason(tmp_path: Path) -> None:
    triage, stays, expected = _fixture_rows()
    _write_gz(tmp_path / "triage.csv.gz", TRIAGE_COLUMNS, triage)
    _write_gz(tmp_path / "edstays.csv.gz", STAY_COLUMNS, stays)

    rows, drops = clean_rows(
        read_table(tmp_path / "triage.csv.gz"),
        read_table(tmp_path / "edstays.csv.gz"),
        random.Random(1),
    )
    assert dict(drops) == expected
    assert len(rows) == 61  # 60 group rows + 1 good row
    assert all(r.origin == "REAL" and r.source_ref.startswith("mimic-iv-ed-demo:stay_id=") for r in rows)
    assert all(r.pain is None or 0 <= r.pain <= 10 for r in rows)
    # 98.6 F went through the conversion.
    assert any(r.temperature_c == 37.0 for r in rows)


# -- Clustering --------------------------------------------------------------


def test_three_zones_ordered_by_admission_rate(built) -> None:
    clusters = built.clusters
    assert [c.index for c in clusters] == [1, 2, 3]
    assert [c.zone for c in clusters] == ["FAR", "INTERMEDIATE", "NEAR"]
    rates = [c.admission_rate for c in clusters]
    assert rates == sorted(rates)
    assert rates[0] < rates[-1]
    assert clusters[0].description.startswith("Lejos del caso critico")
    assert clusters[2].description.startswith("Cerca del caso critico")
    assert "% terminaron hospitalizados" in clusters[2].description


def test_summary_reports_every_k_and_the_drops(built) -> None:
    summary = built.summary
    assert sorted(summary["silhouette_by_k"]) == [2, 3, 4, 5, 6]
    assert sorted(summary["inertia_by_k"]) == [1, 2, 3, 4, 5, 6]
    inertia = [summary["inertia_by_k"][k] for k in range(1, 7)]
    assert inertia == sorted(inertia, reverse=True)
    assert summary["k_used"] == 3
    assert summary["silhouette_k_used"] == summary["silhouette_by_k"][3]
    assert summary["dropped_total"] == 7
    assert summary["privacy"]["nearest_real_distance_zero"] == 0
    assert summary["privacy"]["nearest_real_distance_min"] > 0


def test_build_is_deterministic() -> None:
    triage, stays, _ = _fixture_rows()
    a, b = build_cohort(triage, stays), build_cohort(triage, stays)
    assert a.summary == b.summary
    assert [r.source_ref for r in a.rows] == [r.source_ref for r in b.rows]


# -- Augmentation ------------------------------------------------------------


def test_synthetic_rows_are_new_valid_and_inside_their_cluster(built) -> None:
    real = [r for r in built.rows if r.origin == "REAL"]
    synthetic = [r for r in built.rows if r.origin == "SYNTHETIC"]
    by_id = {r.id: r for r in real}
    real_keys = {r.key() for r in real}

    for cluster in built.clusters:
        assert cluster.n_synthetic == 2 * cluster.n_real > 0

    pattern = re.compile(r"^base=([0-9a-f-]{36});vecino=([0-9a-f-]{36});u=0\.\d\d$")
    for row in synthetic:
        for name in FEATURES:
            low, high = RANGES[name]
            assert low <= getattr(row, name) <= high, name
        assert row.sbp > row.dbp
        assert row.pain is None or 0 <= row.pain <= 10
        assert row.key() not in real_keys

        match = pattern.match(row.source_ref)
        assert match, row.source_ref
        base, neighbour = (by_id[uuid.UUID(g)] for g in match.groups())
        assert row.cluster == base.cluster == neighbour.cluster
        u = float(row.source_ref.rsplit("u=", 1)[1])
        assert row.admitted == (base.admitted if u < 0.5 else neighbour.admitted)


# -- Persistence -------------------------------------------------------------


def _counts(db) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(CohortCluster)),
        db.scalar(select(func.count()).where(CohortRecord.origin == CohortOrigin.REAL)),
        db.scalar(select(func.count()).where(CohortRecord.origin == CohortOrigin.SYNTHETIC)),
    )


def test_persistence_is_idempotent(db, built) -> None:
    assert _counts(db) == (0, 0, 0)
    cohort_service.persist_cohort(db, built)
    first = _counts(db)
    cohort_service.persist_cohort(db, built)
    assert _counts(db) == first
    assert first[0] == 3 and first[1] == 61 and first[2] == 122
    cluster = db.scalar(select(CohortCluster).where(CohortCluster.cluster_index == 3))
    assert cluster.centroid["o2sat"] < 95 and 0 <= cluster.admission_rate <= 1


# -- Endpoint ----------------------------------------------------------------


def test_clusters_endpoint_is_empty_before_the_build(db, client_as) -> None:
    response = client_as("admin").get("/api/v1/cohort/clusters")
    assert response.status_code == 200
    assert response.json() == []


def test_clusters_endpoint_contract(db, client_as, built) -> None:
    cohort_service.persist_cohort(db, built)
    for username in ("admin", "coordinador.eps", "operador.norte", "especialista.norte"):
        response = client_as(username).get("/api/v1/cohort/clusters")
        assert response.status_code == 200, username
    body = response.json()
    assert len(body) >= 2
    assert [c["cluster"] for c in body] == [1, 2, 3]
    assert [c["zona"] for c in body] == ["lejos", "intermedio", "cerca"]
    for item in body:
        assert set(item) == {
            "cluster", "zona", "descripcion", "n_reales", "n_sinteticos", "tasa_ingreso", "silueta",
        }
        assert item["n_sinteticos"] > 0 and item["n_reales"] > 0
        assert item["descripcion"]
    assert sum(c["n_reales"] for c in body) == 61


@pytest.mark.parametrize("username", ["contable.eps", "paciente.demo"])
def test_clusters_endpoint_is_closed_to_accountant_and_patient(db, client_as, username) -> None:
    assert client_as(username).get("/api/v1/cohort/clusters").status_code == 403
