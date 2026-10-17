"""PACS integration (draft): upload validation, the Orthanc call sequence and
who may see an image.

Orthanc is replaced by an in-process fake built on ``httpx.MockTransport``, so
the tests check exactly which requests the API sends without a running PACS.
The DICOM fixture is ``CT_small.dcm`` from the pydicom test data: a small,
anonymised CT slice published for testing.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import struct
import zlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.seed as seed_module
from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models import Base, ImagingStudy, Patient, User
from app.models.enums import ImagingStudyStatus
from app.services import pacs
from app.services.errors import InvalidUploadError, NotFoundError, PacsUnavailableError

DICOM = (Path(__file__).parent / "fixtures" / "CT_small.dcm").read_bytes()
ORIGINAL = "aaaaaaaa-11111111-22222222-33333333-44444444"
REWRITTEN = "bbbbbbbb-11111111-22222222-33333333-44444444"
CREATED = "cccccccc-11111111-22222222-33333333-44444444"
STUDY = "dddddddd-11111111-22222222-33333333-44444444"
PREVIEW = b"\x89PNG\r\n\x1a\n" + b"preview"


def make_png(width: int, height: int) -> bytes:
    """A real, minimal greyscale PNG, built without an imaging library."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    raw = b"".join(b"\x00" + bytes(width) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class FakeOrthanc:
    """Records every request and answers like Orthanc would."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bodies: dict[str, object] = {}
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("sin conexion", request=request)
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "POST" and path == "/instances":
            stored = REWRITTEN if request.content.startswith(b"REWRITTEN") else ORIGINAL
            return httpx.Response(200, json={"ID": stored, "Status": "Success"})
        if request.method == "POST" and path == f"/instances/{ORIGINAL}/modify":
            self.bodies["modify"] = json.loads(request.content)
            return httpx.Response(200, content=b"REWRITTEN" + DICOM)
        if request.method == "DELETE" and path == f"/instances/{ORIGINAL}":
            return httpx.Response(200, json={})
        if request.method == "POST" and path == "/tools/create-dicom":
            self.bodies["create"] = json.loads(request.content)
            return httpx.Response(200, json={"ID": CREATED, "Path": f"/instances/{CREATED}"})
        if request.method == "GET" and path.endswith("/study"):
            return httpx.Response(200, json={"ID": STUDY})
        if request.method == "GET" and path == f"/studies/{STUDY}/instances":
            return httpx.Response(200, json=[{"ID": REWRITTEN, "MainDicomTags": {"InstanceNumber": "1"}}])
        if request.method == "GET" and path.endswith("/preview"):
            return httpx.Response(200, content=PREVIEW, headers={"Content-Type": "image/png"})
        return httpx.Response(404)


@pytest.fixture
def orthanc(monkeypatch: pytest.MonkeyPatch) -> FakeOrthanc:
    fake = FakeOrthanc()
    monkeypatch.setattr(
        pacs,
        "_client",
        lambda: httpx.Client(base_url="http://orthanc", transport=httpx.MockTransport(fake.handler)),
    )
    return fake


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_the_fixture_is_recognised_as_dicom() -> None:
    assert pacs.sniff(DICOM) == "dicom"


def test_the_type_comes_from_the_bytes_not_the_name() -> None:
    assert pacs.sniff(make_png(4, 4)) == "png"
    assert pacs.sniff(b"MZ\x90\x00 este es un ejecutable llamado foto.png") is None


def test_png_dimensions_are_read_from_the_header() -> None:
    assert pacs.image_size(make_png(37, 21), "png") == (37, 21)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "vacio"),
        (b"MZ" + bytes(200), "Solo se aceptan"),
        (make_png(pacs.MAX_DIMENSION + 1, 1), "como maximo"),
    ],
)
def test_bad_uploads_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(InvalidUploadError, match=message):
        pacs.validate_upload(data)


def test_an_oversized_upload_is_rejected() -> None:
    with pytest.raises(InvalidUploadError, match="maximo"):
        pacs.validate_upload(b"\x89PNG\r\n\x1a\n" + bytes(pacs.MAX_UPLOAD_BYTES))


def test_ids_that_could_escape_the_url_are_refused() -> None:
    with pytest.raises(NotFoundError):
        pacs.check_id("../../system")


# ---------------------------------------------------------------------------
# Orthanc call sequence
# ---------------------------------------------------------------------------


class _Study:
    study_instance_uid = "1.2.826.0.1.3680043.8.498.42"
    accession_number = "ACC-2026-0000042"
    description = "Radiografia de torax"
    body_site = "Torax"
    modality = "DX"


class _Patient:
    document_number = "1032450137"
    first_name = "Camila"
    last_name = "Ramirez"
    gender = "female"

    class birth_date:  # noqa: N801
        @staticmethod
        def strftime(_fmt: str) -> str:
            return "19800101"


def test_a_foreign_dicom_is_rewritten_with_our_identifiers(orthanc: FakeOrthanc) -> None:
    """Stored, rewritten with our patient and study, stored again, and the
    original copy deleted: the PACS never keeps it under a foreign identity."""

    result = pacs.store_for_study(_Study(), _Patient(), DICOM)

    assert result == {"instance_id": REWRITTEN, "pacs_study_id": STUDY}
    assert orthanc.calls[:4] == [
        ("POST", "/instances"),
        ("POST", f"/instances/{ORIGINAL}/modify"),
        ("POST", "/instances"),
        ("DELETE", f"/instances/{ORIGINAL}"),
    ]
    replace = orthanc.bodies["modify"]["Replace"]
    assert replace["PatientID"] == "1032450137"
    assert replace["StudyInstanceUID"] == _Study.study_instance_uid
    assert replace["PatientName"] == "Ramirez^Camila"


def test_a_png_is_converted_with_our_identifiers(orthanc: FakeOrthanc) -> None:
    result = pacs.store_for_study(_Study(), _Patient(), make_png(8, 8))

    assert result["instance_id"] == CREATED
    body = orthanc.bodies["create"]
    assert body["Content"].startswith("data:image/png;base64,")
    assert base64.b64decode(body["Content"].split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"
    assert body["Tags"]["PatientID"] == "1032450137"
    assert body["Tags"]["StudyInstanceUID"] == _Study.study_instance_uid
    assert body["Tags"]["Modality"] == "DX"
    # Sin Force, Orthanc 1.12 rechaza fijar StudyInstanceUID (probado contra el
    # servidor real: "Trying to override a value inherited from a parent module").
    assert body["Force"] is True


def test_a_pacs_that_is_down_is_reported_not_crashed(orthanc: FakeOrthanc) -> None:
    orthanc.down = True
    with pytest.raises(PacsUnavailableError):
        pacs.list_instances(STUDY)
    assert pacs.system_status().available is False


# ---------------------------------------------------------------------------
# Routes: who may upload and who may see
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(connection, _record) -> None:  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    original = seed_module.SessionLocal
    seed_module.SessionLocal = factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            seed_module.seed(reset=True, patient_count=40)
    finally:
        seed_module.SessionLocal = original
    with factory() as session:
        yield session


@pytest.fixture
def client_as(db: Session):
    def build(username: str) -> TestClient:
        account = db.scalar(select(User).where(User.username == username))
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: account
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def _study_of(db: Session, username: str) -> ImagingStudy:
    operator = db.scalar(select(User).where(User.username == username))
    return db.scalar(
        select(ImagingStudy).where(ImagingStudy.organization_id == operator.organization_id).limit(1)
    )


def test_an_operator_uploads_to_a_study_of_their_ips(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    study = _study_of(db, "operador.norte")
    response = client_as("operador.norte").post(
        f"/api/v1/imaging-studies/{study.id}/images", content=DICOM,
        headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["pacs_study_id"] == STUDY

    db.expire_all()
    stored = db.get(ImagingStudy, study.id)
    assert stored.pacs_study_id == STUDY
    assert stored.status == ImagingStudyStatus.AVAILABLE


def test_uploads_are_for_clinical_staff_only(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    study = _study_of(db, "operador.norte")
    response = client_as("coordinador.eps").post(
        f"/api/v1/imaging-studies/{study.id}/images", content=DICOM
    )
    assert response.status_code == 403


def test_an_operator_cannot_upload_to_another_ips(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    study = _study_of(db, "operador.sur")
    response = client_as("operador.norte").post(
        f"/api/v1/imaging-studies/{study.id}/images", content=DICOM
    )
    assert response.status_code == 403


def test_a_file_that_is_not_an_image_is_rejected(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    study = _study_of(db, "operador.norte")
    response = client_as("operador.norte").post(
        f"/api/v1/imaging-studies/{study.id}/images", content=b"MZ" + bytes(500)
    )
    assert response.status_code == 422
    assert orthanc.calls == []


def _linked_study(db: Session) -> ImagingStudy:
    patient_account = db.scalar(select(User).where(User.username == "paciente.demo"))
    study = db.scalar(select(ImagingStudy).where(ImagingStudy.patient_id == patient_account.patient_id))
    if study is None:
        study = db.scalar(select(ImagingStudy).limit(1))
        study.patient_id = patient_account.patient_id
    study.pacs_study_id = STUDY
    db.commit()
    return study


def test_the_owner_patient_sees_the_preview(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    _linked_study(db)
    response = client_as("paciente.demo").get(f"/api/v1/imaging/instances/{REWRITTEN}/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content == PREVIEW


def test_another_patient_gets_not_found_not_forbidden(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    """A 403 would confirm the image exists."""

    study = _linked_study(db)
    other = db.scalar(select(Patient).where(Patient.id != study.patient_id).limit(1))
    stranger = db.scalar(select(User).where(User.username == "paciente.demo"))
    stranger.patient_id = other.id
    db.commit()
    response = client_as("paciente.demo").get(f"/api/v1/imaging/instances/{REWRITTEN}/preview")
    assert response.status_code == 404


def test_a_malformed_instance_id_never_reaches_the_pacs(db: Session, client_as, orthanc: FakeOrthanc) -> None:
    response = client_as("admin").get("/api/v1/imaging/instances/..%2F..%2Fsystem/preview")
    assert response.status_code == 404
    assert orthanc.calls == []


def test_without_a_configured_password_the_pacs_is_reported_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No known default credential in the code: a missing ORTHANC_PASSWORD
    makes the PACS unavailable instead of connecting with a public password."""

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "orthanc_password", None)
    status = pacs.system_status()
    assert status.available is False
    assert "ORTHANC_PASSWORD" in status.detail


# ---------------------------------------------------------------------------
# GET /patients/{id}/imagenes: downloadable images (R06 contract)
# ---------------------------------------------------------------------------


def _with_pixels(db: Session, username: str = "operador.norte") -> ImagingStudy:
    study = _study_of(db, username)
    study.pacs_study_id = STUDY
    db.commit()
    return study


def test_the_image_list_gives_instance_ids_and_working_urls(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _with_pixels(db)
    client = client_as("operador.norte")
    response = client.get(f"/api/v1/patients/{study.patient_id}/imagenes")
    assert response.status_code == 200, response.text
    (image,) = response.json()["imagenes"]
    assert image["id"] == REWRITTEN  # an instance id, not a study id
    assert image["id"] != str(study.id)
    assert image["url"] == f"/api/v1/imaging/instances/{REWRITTEN}/preview"
    assert image["url_absoluta"] == f"http://testserver{image['url']}"
    assert image["estudio_id"] == str(study.id)
    assert image["modalidad"] == study.modality.value
    assert image["fecha"] is not None and image["tipo_contenido"] == "image/png"

    # Following the url (same token) downloads the image.
    download = client.get(image["url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/png"
    assert download.content == PREVIEW


def test_the_absolute_url_honours_the_forwarded_scheme(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _with_pixels(db)
    response = client_as("operador.norte").get(
        f"/api/v1/patients/{study.patient_id}/imagenes",
        headers={"X-Forwarded-Proto": "https", "Host": "api.example.test"},
    )
    (image,) = response.json()["imagenes"]
    assert image["url_absoluta"] == f"https://api.example.test{image['url']}"


def test_a_study_without_pixels_or_gone_from_the_pacs_is_not_listed(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _study_of(db, "operador.norte")  # registered only: no pacs_study_id
    client = client_as("operador.norte")
    assert client.get(f"/api/v1/patients/{study.patient_id}/imagenes").json() == {"imagenes": []}
    assert orthanc.calls == []

    study.pacs_study_id = "ffffffff-11111111-22222222-33333333-44444444"  # the PACS answers 404
    db.commit()
    assert client.get(f"/api/v1/patients/{study.patient_id}/imagenes").json() == {"imagenes": []}


def test_the_image_list_respects_scope_and_roles(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _with_pixels(db)
    url = f"/api/v1/patients/{study.patient_id}/imagenes"
    # Another IPS: nothing leaves the database, and the download is a 404 (not 403).
    assert client_as("operador.sur").get(url).json() == {"imagenes": []}
    assert client_as("operador.sur").get(
        f"/api/v1/imaging/instances/{REWRITTEN}/preview"
    ).status_code == 404
    # Roles outside the clinical staff.
    assert client_as("contable.eps").get(url).status_code == 403
    # Unknown patient.
    assert client_as("operador.norte").get(
        "/api/v1/patients/00000000-0000-0000-0000-000000000001/imagenes"
    ).status_code == 404


def test_a_patient_lists_only_their_own_images(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _linked_study(db)
    client = client_as("paciente.demo")
    (image,) = client.get(f"/api/v1/patients/{study.patient_id}/imagenes").json()["imagenes"]
    assert client.get(image["url"]).status_code == 200

    other = db.scalar(select(Patient).where(Patient.id != study.patient_id).limit(1))
    assert client.get(f"/api/v1/patients/{other.id}/imagenes").status_code == 404


def test_the_image_list_answers_503_when_the_pacs_is_down(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _with_pixels(db)
    orthanc.down = True
    response = client_as("operador.norte").get(f"/api/v1/patients/{study.patient_id}/imagenes")
    assert response.status_code == 503
    assert "servidor de imagenes" in response.json()["detail"]


def _clone_studies(db: Session, study: ImagingStudy, count: int) -> list[str]:
    """``count`` more studies of the same patient, each with its own PACS study id."""

    ids = []
    for i in range(count):
        pacs_id = f"{i + 1:08x}-11111111-22222222-33333333-44444444"
        db.add(
            ImagingStudy(
                patient_id=study.patient_id,
                organization_id=study.organization_id,
                study_instance_uid=f"1.2.840.7777.{i + 1}",
                accession_number=f"CLONE-{i + 1}",
                modality=study.modality,
                status=study.status,
                body_site="Torax",
                series_count=1,
                instance_count=1,
                started_at=study.started_at,
                pacs_study_id=pacs_id,
            )
        )
        ids.append(pacs_id)
    db.commit()
    return ids


def test_a_patient_with_more_than_twenty_studies_loses_no_images(
    db: Session, client_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    study = _study_of(db, "operador.norte")
    _clone_studies(db, study, 24)
    monkeypatch.setattr(
        pacs, "list_instances", lambda study_id: [{"instance_id": f"i-{study_id}", "number": 1}]
    )
    response = client_as("operador.norte").get(f"/api/v1/patients/{study.patient_id}/imagenes")
    assert response.status_code == 200
    ids = [i["id"] for i in response.json()["imagenes"]]
    assert len(ids) >= 24 and len(set(ids)) == len(ids)  # past the old default page of 20


def test_one_failing_study_is_skipped_but_all_failing_is_a_503(
    db: Session, client_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    study = _study_of(db, "operador.norte")
    bad, good = _clone_studies(db, study, 2)
    study.pacs_study_id = None  # the registered-only original has no pixels
    db.commit()

    def flaky(study_id: str) -> list[dict[str, object]]:
        if study_id == bad:
            raise PacsUnavailableError("El servidor de imagenes respondio HTTP 500")
        return [{"instance_id": "ok-instance", "number": 1}]

    monkeypatch.setattr(pacs, "list_instances", flaky)
    client = client_as("operador.norte")
    url = f"/api/v1/patients/{study.patient_id}/imagenes"
    response = client.get(url)
    assert response.status_code == 200
    assert [i["id"] for i in response.json()["imagenes"]] == ["ok-instance"]

    def broken(_study_id: str) -> list[dict[str, object]]:
        raise PacsUnavailableError("El servidor de imagenes no responde: ConnectError")

    monkeypatch.setattr(pacs, "list_instances", broken)
    assert client.get(url).status_code == 503
    assert good  # silence unused: both ids were created


def test_a_spoofed_forwarded_header_or_host_never_reaches_url_absoluta(
    db: Session, client_as, orthanc: FakeOrthanc
) -> None:
    study = _with_pixels(db)
    url = f"/api/v1/patients/{study.patient_id}/imagenes"
    client = client_as("operador.norte")

    (image,) = client.get(url, headers={"X-Forwarded-Proto": "javascript"}).json()["imagenes"]
    assert image["url_absoluta"] == f"http://testserver{image['url']}"

    (image,) = client.get(url, headers={"Host": "evil.test/x?y=1"}).json()["imagenes"]
    assert image["url_absoluta"] is None and image["url"].startswith("/api/v1/imaging/")
