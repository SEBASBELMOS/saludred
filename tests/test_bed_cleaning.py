"""Bed cleaning evidence: checklist, photo, human review and the availability gate.

Same setup as the bed rules tests: the synthetic dataset in SQLite in memory and
the real FastAPI app with the database and the user swapped in.
"""

from __future__ import annotations

import struct
import uuid
import zlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, BedCleaningRecord, BedStatusEvent, Location
from app.models.cleaning import CHECKLIST_ITEMS
from app.models.enums import AuditAction, BedStatus, LocationType
from app.services import beds as beds_service
from app.services.errors import ConflictError

# Reusa la base de pruebas de las reglas de camas (dataset sintetico en SQLite).
from tests.test_bed_rules import _user, client_as, db  # noqa: F401


def _png(width: int = 1, height: int = 1) -> bytes:
    """A minimal valid PNG generated on the fly (no external files)."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"\x00" + b"\xff\xff\xff" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw * height))
        + chunk(b"IEND", b"")
    )


def _payload(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    values = {
        "started_at": (now - timedelta(minutes=40)).isoformat(),
        "finished_at": now.isoformat(),
        "isolation": "NONE",
        "disinfectant": "QUATERNARY_AMMONIUM",
        "curtains_changed": False,
        "checklist": {key: True for key in CHECKLIST_ITEMS},
        "notes": "Limpieza terminal",
    }
    values.update(overrides)
    return values


def _cleaning_bed(db, username: str = "operador.norte") -> Location:
    """A bed of the user's IPS moved to CLEANING through the real service."""

    operator = _user(db, username)
    bed = db.scalar(
        select(Location).where(
            Location.location_type == LocationType.BED,
            Location.status == BedStatus.AVAILABLE,
            Location.organization_id == operator.organization_id,
        )
    )
    assert bed is not None
    beds_service.change_bed_status(db, bed, BedStatus.CLEANING, actor=operator)
    return bed


def _create(client, bed, **overrides):
    return client.post(f"/api/v1/beds/{bed.id}/cleaning", json=_payload(**overrides))


def _with_photo(db, client_as, bed, username: str = "operador.norte") -> str:
    client = client_as(username)
    record_id = _create(client, bed).json()["id"]
    response = client.put(f"/api/v1/cleaning/{record_id}/photo", content=_png())
    assert response.status_code == 200, response.text
    return record_id


def test_creating_a_record_for_a_bed_in_cleaning(db, client_as) -> None:
    bed = _cleaning_bed(db)
    response = _create(client_as("operador.norte"), bed)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["has_photo"] is False
    assert "photo" not in body

    entry = db.scalar(
        select(AuditLog).where(AuditLog.action == AuditAction.CLEANING_EVIDENCE)
    )
    assert entry.metadata_json == {
        "bed_id": str(bed.id),
        "record_id": body["id"],
        "action": "created",
    }


def test_a_bed_that_is_not_in_cleaning_takes_no_record(db, client_as) -> None:
    bed = _cleaning_bed(db)
    beds_service.change_bed_status(db, bed, BedStatus.AVAILABLE, actor=_user(db, "admin"))
    assert _create(client_as("operador.norte"), bed).status_code == 409


def test_an_incomplete_checklist_is_rejected(db, client_as) -> None:
    bed = _cleaning_bed(db)
    checklist = {key: True for key in CHECKLIST_ITEMS}
    checklist["piso"] = False
    del checklist["bano"]
    response = _create(client_as("operador.norte"), bed, checklist=checklist)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("La limpieza no esta completa: faltan ")
    assert "bano" in response.json()["detail"] and "piso" in response.json()["detail"]


def test_isolation_requires_curtains_changed(db, client_as) -> None:
    bed = _cleaning_bed(db)
    response = _create(client_as("operador.norte"), bed, isolation="CONTACT")
    assert response.status_code == 422
    ok = _create(client_as("operador.norte"), bed, isolation="CONTACT", curtains_changed=True)
    assert ok.status_code == 201, ok.text


def test_spores_require_a_sporicidal_disinfectant(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    bad = _create(client, bed, isolation="SPORES", curtains_changed=True)
    assert bad.status_code == 422
    good = _create(
        client, bed, isolation="SPORES", curtains_changed=True, disinfectant="SPORICIDAL"
    )
    assert good.status_code == 201, good.text


def test_an_operator_of_another_ips_is_forbidden(db, client_as) -> None:
    bed = _cleaning_bed(db, "operador.norte")
    assert _create(client_as("operador.sur"), bed).status_code == 403
    assert client_as("operador.sur").get(f"/api/v1/beds/{bed.id}/cleaning").status_code == 403


def test_a_coordinator_cannot_register_a_cleaning(db, client_as) -> None:
    bed = _cleaning_bed(db)
    assert _create(client_as("coordinador.eps"), bed).status_code == 403


def test_photo_upload_accepts_png(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    record_id = _create(client, bed).json()["id"]

    response = client.put(f"/api/v1/cleaning/{record_id}/photo", content=_png())
    assert response.status_code == 200, response.text
    assert response.json()["has_photo"] is True
    assert response.json()["photo_content_type"] == "image/png"

    photo = client.get(f"/api/v1/cleaning/{record_id}/photo")
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/png"
    assert photo.content == _png()


def test_photo_upload_rejects_a_text_file(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    record_id = _create(client, bed).json()["id"]
    response = client.put(f"/api/v1/cleaning/{record_id}/photo", content=b"esto no es una foto")
    assert response.status_code == 422


def test_photo_upload_rejects_more_than_five_megabytes(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    record_id = _create(client, bed).json()["id"]
    big = _png() + b"\x00" * (5 * 1024 * 1024)
    response = client.put(f"/api/v1/cleaning/{record_id}/photo", content=big)
    assert response.status_code == 422


def test_photo_upload_rejects_oversized_dimensions(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    record_id = _create(client, bed).json()["id"]
    header_only = _png()[:16] + struct.pack(">II", 5000, 10) + _png()[24:]
    response = client.put(f"/api/v1/cleaning/{record_id}/photo", content=header_only)
    assert response.status_code == 422


def test_only_the_author_or_an_admin_uploads_the_photo(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _create(client_as("operador.norte"), bed).json()["id"]
    other = client_as("operador.sur").put(
        f"/api/v1/cleaning/{record_id}/photo", content=_png()
    )
    assert other.status_code == 403
    admin = client_as("admin").put(f"/api/v1/cleaning/{record_id}/photo", content=_png())
    assert admin.status_code == 200, admin.text


def test_approving_without_a_photo_is_a_conflict(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _create(client_as("operador.norte"), bed).json()["id"]
    response = client_as("coordinador.eps").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Falta la foto de evidencia"


def test_the_author_cannot_approve_their_own_record(db, client_as) -> None:
    bed = _cleaning_bed(db)
    admin = client_as("admin")
    record_id = _create(admin, bed).json()["id"]
    admin.put(f"/api/v1/cleaning/{record_id}/photo", content=_png())
    response = admin.post(f"/api/v1/cleaning/{record_id}/review", json={"approved": True})
    assert response.status_code == 403
    assert response.json()["detail"] == "Quien registra la limpieza no puede aprobarla"


def test_an_operator_cannot_review(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _with_photo(db, client_as, bed)
    response = client_as("operador.norte").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )
    assert response.status_code == 403


def test_a_coordinator_approves_and_it_is_audited(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _with_photo(db, client_as, bed)
    response = client_as("coordinador.eps").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "APPROVED"
    assert response.json()["reviewed_by"] is not None

    actions = [
        entry.metadata_json["action"]
        for entry in db.scalars(
            select(AuditLog)
            .where(AuditLog.action == AuditAction.CLEANING_EVIDENCE)
            .order_by(AuditLog.created_at)
        )
    ]
    assert set(actions) >= {"created", "approved"}


def test_rejecting_requires_notes(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _with_photo(db, client_as, bed)
    coordinator = client_as("coordinador.eps")
    missing = coordinator.post(f"/api/v1/cleaning/{record_id}/review", json={"approved": False})
    assert missing.status_code == 422
    rejected = coordinator.post(
        f"/api/v1/cleaning/{record_id}/review",
        json={"approved": False, "review_notes": "Barandas con residuos"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"


def test_a_reviewed_record_cannot_be_reviewed_or_photographed_again(db, client_as) -> None:
    bed = _cleaning_bed(db)
    record_id = _with_photo(db, client_as, bed)
    coordinator = client_as("coordinador.eps")
    coordinator.post(f"/api/v1/cleaning/{record_id}/review", json={"approved": True})
    again = coordinator.post(f"/api/v1/cleaning/{record_id}/review", json={"approved": True})
    assert again.status_code == 409
    photo = client_as("operador.norte").put(
        f"/api/v1/cleaning/{record_id}/photo", content=_png()
    )
    assert photo.status_code == 409


def test_records_are_listed_newest_first(db, client_as) -> None:
    bed = _cleaning_bed(db)
    client = client_as("operador.norte")
    first = _create(client, bed).json()["id"]
    second = _create(client, bed).json()["id"]
    listed = client.get(f"/api/v1/beds/{bed.id}/cleaning")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [second, first]


def test_the_gate_is_off_by_default(db) -> None:
    assert get_settings().require_cleaning_evidence is False
    bed = _cleaning_bed(db)
    beds_service.change_bed_status(db, bed, BedStatus.AVAILABLE, actor=_user(db, "operador.norte"))
    assert bed.status == BedStatus.AVAILABLE


def test_the_gate_blocks_availability_until_a_record_is_approved(
    db, client_as, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "require_cleaning_evidence", True)
    bed = _cleaning_bed(db)
    operator = _user(db, "operador.norte")

    with pytest.raises(ConflictError, match="evidencia de limpieza aprobada"):
        beds_service.change_bed_status(db, bed, BedStatus.AVAILABLE, actor=operator)

    # Un registro pendiente tampoco alcanza.
    record_id = _with_photo(db, client_as, bed)
    response = client_as("operador.norte").post(
        f"/api/v1/beds/{bed.id}/status", json={"new_status": "AVAILABLE"}
    )
    assert response.status_code == 409

    client_as("coordinador.eps").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )
    response = client_as("operador.norte").post(
        f"/api/v1/beds/{bed.id}/status", json={"new_status": "AVAILABLE"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "AVAILABLE"


def test_evidence_from_a_previous_cleaning_does_not_count(db, client_as, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "require_cleaning_evidence", True)
    bed = _cleaning_bed(db)
    record_id = _with_photo(db, client_as, bed)
    client_as("coordinador.eps").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )
    # Un ciclo nuevo: la cama vuelve a limpieza despues de la aprobacion.
    record = db.get(BedCleaningRecord, uuid.UUID(record_id))
    db.add(
        BedStatusEvent(
            location_id=bed.id,
            previous_status=BedStatus.AVAILABLE,
            new_status=BedStatus.CLEANING,
            event_at=record.created_at + timedelta(minutes=5),
        )
    )
    db.commit()
    with pytest.raises(ConflictError):
        beds_service.change_bed_status(
            db, bed, BedStatus.AVAILABLE, actor=_user(db, "operador.norte")
        )


def test_overdue_lists_beds_without_approved_evidence(db, client_as) -> None:
    dirty = _cleaning_bed(db, "operador.norte")
    clean = _cleaning_bed(db, "operador.sur")
    record_id = _with_photo(db, client_as, clean, "operador.sur")
    client_as("coordinador.eps").post(
        f"/api/v1/cleaning/{record_id}/review", json={"approved": True}
    )

    response = client_as("coordinador.eps").get("/api/v1/beds/cleaning/overdue?minutes=0")
    assert response.status_code == 200, response.text
    ids = {item["location_id"] for item in response.json()}
    assert str(dirty.id) in ids
    assert str(clean.id) not in ids


def test_overdue_respects_the_minutes_threshold(db, client_as) -> None:
    bed = _cleaning_bed(db)
    response = client_as("coordinador.eps").get("/api/v1/beds/cleaning/overdue?minutes=60")
    assert str(bed.id) not in {item["location_id"] for item in response.json()}


def test_overdue_is_for_admin_and_coordinator_only(db, client_as) -> None:
    assert client_as("operador.norte").get("/api/v1/beds/cleaning/overdue").status_code == 403
    assert client_as("admin").get("/api/v1/beds/cleaning/overdue").status_code == 200
