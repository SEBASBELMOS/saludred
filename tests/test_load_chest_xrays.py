"""Selection logic of the chest X-ray loader, on fake rows (no pyarrow, no network)."""

from __future__ import annotations

import csv
import json

import pytest

from scripts.load_chest_xrays import (
    Candidate,
    LabelMetadataError,
    Study,
    assign_studies,
    label_names_from_metadata,
    select_images,
    to_candidates,
    write_manifest,
)

NAMES = ["Atelectasis", "Cardiomegaly", "Effusion", "Pneumonia", "Edema", "No Finding", "Nodule"]


def _rows() -> list[dict]:
    """120 fake rows: minors, critical, mixed, normal and non-critical findings."""

    rows = []
    for i in range(120):
        kind = i % 6
        labels = {
            0: [3],          # Pneumonia
            1: [5],          # No Finding
            2: [1, 2],       # Cardiomegaly + Effusion
            3: [5],          # No Finding
            4: [0, 6],       # Atelectasis + Nodule (neither critical nor normal)
            5: [5, 6],       # No Finding + Nodule (not "only" No Finding)
        }[kind]
        rows.append(
            {"scan_id": f"scan-{i}", "age": 10 if i % 10 == 0 else 30 + i % 50, "sex": "F", "labels": labels}
        )
    return rows


@pytest.fixture
def candidates() -> list[Candidate]:
    return to_candidates(_rows(), NAMES)


def test_selection_keeps_adults_and_the_right_labels(candidates) -> None:
    selection = select_images(candidates, 10, 10, seed=1)
    assert len(selection.critical) == 10 and len(selection.normal) == 10
    for c in selection.critical + selection.normal:
        assert c.age >= 18
    for c in selection.critical:
        assert set(c.labels) & {"Pneumonia", "Effusion", "Edema", "Consolidation", "Cardiomegaly"}
    for c in selection.normal:
        assert c.labels == ("No Finding",)


def test_selection_is_deterministic_and_seed_dependent(candidates) -> None:
    a = select_images(candidates, 10, 10, seed=2026)
    b = select_images(list(reversed(candidates)), 10, 10, seed=2026)
    assert [c.row for c in a.critical] == [c.row for c in b.critical]
    assert [c.row for c in a.normal] == [c.row for c in b.normal]
    other = select_images(candidates, 10, 10, seed=7)
    assert [c.row for c in a.critical] != [c.row for c in other.critical]


def test_selection_returns_fewer_when_the_pool_is_small(candidates) -> None:
    selection = select_images(candidates, 500, 500, seed=1)
    assert 0 < len(selection.critical) < 500
    assert len({c.row for c in selection.critical}) == len(selection.critical)


def _studies(n: int) -> list[Study]:
    profiles = ["RESPIRATORY", "CARDIAC", "ELDERLY_FRAIL", "HEALTHY", "HYPERTENSIVE", "DIABETIC"]
    return [Study(f"s{i:03d}", f"p{i:03d}", profiles[i % 6]) for i in range(n)]


def test_one_image_per_study_and_profile_preference(candidates) -> None:
    selection = select_images(candidates, 10, 10, seed=1)
    assignments = assign_studies(selection, _studies(60), seed=1)
    assert len(assignments) == 20
    assert len({a.study.study_id for a in assignments}) == 20
    assert all(a.study.profile in ("RESPIRATORY", "CARDIAC", "ELDERLY_FRAIL")
               for a in assignments if a.group == "critical")
    assert all(a.study.profile in ("HEALTHY", "HYPERTENSIVE")
               for a in assignments if a.group == "normal")
    again = assign_studies(selection, _studies(60), seed=1)
    assert [(a.study.study_id, a.candidate.row) for a in assignments] == [
        (a.study.study_id, a.candidate.row) for a in again
    ]


def test_fewer_studies_than_images_assigns_only_what_fits(candidates) -> None:
    selection = select_images(candidates, 10, 10, seed=1)
    assignments = assign_studies(selection, _studies(6), seed=1)
    assert len(assignments) == 6
    assert len({a.study.study_id for a in assignments}) == 6


def test_manifest_columns(tmp_path, candidates) -> None:
    selection = select_images(candidates, 2, 2, seed=1)
    assignments = assign_studies(selection, _studies(10), seed=1)
    for a in assignments:
        a.http_status = "dry-run"
    path = tmp_path / "manifest.csv"
    write_manifest(path, assignments)
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert list(rows[0]) == [
        "study_id", "patient_id", "nih_scan_id", "labels", "age", "sex", "http_status",
    ]
    assert len(rows) == 4 and rows[0]["http_status"] == "dry-run"


def test_label_names_come_from_the_huggingface_metadata() -> None:
    features = {"info": {"features": {"labels": {"feature": {"names": NAMES, "_type": "ClassLabel"}, "_type": "Sequence"}}}}
    meta = {b"huggingface": json.dumps(features).encode()}
    assert label_names_from_metadata(meta) == NAMES


@pytest.mark.parametrize("meta", [None, {}, {b"huggingface": b"{}"},
                                  {b"huggingface": b'{"info": {"features": {"labels": {"dtype": "int64"}}}}'}])
def test_missing_label_metadata_is_a_clear_error(meta) -> None:
    with pytest.raises(LabelMetadataError):
        label_names_from_metadata(meta)


def test_label_index_outside_the_list_fails() -> None:
    with pytest.raises(LabelMetadataError):
        to_candidates([{"labels": [99], "age": 40}], NAMES)
