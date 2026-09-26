"""The only client of the PACS (Orthanc).

Architectural rule: the browser never talks to Orthanc. Only this API does,
and only after checking the token and the role. Orthanc knows nothing about
our roles and writes nothing to our audit log, so every access has to come
through here for the access control and the traceability to hold.

Draft scope, pending confirmation with the course: store an image for a study
that already exists in our database, list its images and serve a preview.
The study row is registered first (metadata); the pixels come later. What ties
the two worlds together are two DICOM tags written on every stored instance:
``PatientID`` carries the patient's document number and ``StudyInstanceUID``
carries the UID of our study row.

Limit worth knowing: Orthanc renders previews as 8-bit PNG (256 grey levels).
A real CT holds 12-16 bits and is read with windowing; the viewer here works
on the preview, which is enough to show the flow but not for diagnosis.
"""

from __future__ import annotations

import base64
import re
import struct
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.services.errors import InvalidUploadError, NotFoundError, PacsUnavailableError

# Upload defences, the same ones taught in the course notebook.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_DIMENSION = 4096

# Orthanc identifiers are SHA-1 based: five groups of eight hex digits. Checking
# the shape before building a URL keeps "../../something" out of every request.
ORTHANC_ID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{8}){4}$")


@dataclass(frozen=True)
class PacsStatus:
    available: bool
    detail: str


def _client() -> httpx.Client:
    """One place builds the connection, so tests can replace it."""

    settings = get_settings()
    if not settings.orthanc_password:
        raise PacsUnavailableError("El servidor de imagenes no esta configurado: falta ORTHANC_PASSWORD")
    return httpx.Client(
        base_url=settings.orthanc_url.rstrip("/"),
        auth=(settings.orthanc_user, settings.orthanc_password),
        timeout=settings.orthanc_timeout_seconds,
    )


def _request(method: str, path: str, **kwargs) -> httpx.Response:  # noqa: ANN003
    try:
        with _client() as client:
            response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise PacsUnavailableError(f"El servidor de imagenes no responde: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise PacsUnavailableError("El servidor de imagenes rechazo las credenciales de la API")
    if response.status_code == 404:
        raise NotFoundError("Imagen no encontrada en el servidor de imagenes")
    if response.status_code == 400:
        # Orthanc explica el rechazo en "Message" y "Details"; sin eso, un
        # error de integracion se vuelve una adivinanza.
        try:
            reason = response.json()
            detail = f": {reason.get('Message', '')} ({reason.get('Details', '')})"
        except ValueError:
            detail = ""
        raise InvalidUploadError(f"El servidor de imagenes rechazo el archivo{detail}")
    if response.status_code >= 400:
        raise PacsUnavailableError(f"El servidor de imagenes respondio HTTP {response.status_code}")
    return response


def check_id(value: str) -> str:
    if not ORTHANC_ID.match(value or ""):
        raise NotFoundError("Imagen no encontrada")
    return value


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def system_status() -> PacsStatus:
    """Ask the PACS who it is, as a status rather than an exception."""

    try:
        body = _request("GET", "/system").json()
    except (PacsUnavailableError, NotFoundError, InvalidUploadError) as exc:
        return PacsStatus(False, str(exc))
    return PacsStatus(True, f"{body.get('Name', 'Orthanc')} version {body.get('Version', '?')}")


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


def sniff(data: bytes) -> str | None:
    """Identify the file by its bytes, never by its name or declared type.

    A file called ``photo.png`` can be anything; the signature cannot lie as
    easily.
    """

    if len(data) > 132 and data[128:132] == b"DICM":
        return "dicom"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def image_size(data: bytes, kind: str) -> tuple[int, int] | None:
    """Width and height read from the header, without decoding the image."""

    if kind == "png" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if kind == "jpeg":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            # Start-of-frame markers carry the dimensions.
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return width, height
            i += 2 + length
    return None


def validate_upload(data: bytes) -> str:
    if not data:
        raise InvalidUploadError("El archivo esta vacio")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidUploadError(f"El archivo supera el maximo de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    kind = sniff(data)
    if kind is None:
        raise InvalidUploadError("Solo se aceptan archivos DICOM, PNG o JPEG")
    if kind in ("png", "jpeg"):
        size = image_size(data, kind)
        if size is None:
            raise InvalidUploadError("No se pudieron leer las dimensiones de la imagen")
        if max(size) > MAX_DIMENSION or min(size) < 1:
            raise InvalidUploadError(f"La imagen debe medir como maximo {MAX_DIMENSION} px por lado")
    return kind


# ---------------------------------------------------------------------------
# Store, list, preview
# ---------------------------------------------------------------------------


def _dicom_tags(study, patient) -> dict[str, str]:  # noqa: ANN001
    sex = {"male": "M", "female": "F"}.get(getattr(patient.gender, "value", patient.gender), "O")
    return {
        # The link with our database: document number and our study UID.
        "PatientID": patient.document_number,
        "PatientName": f"{patient.last_name}^{patient.first_name}",
        "PatientBirthDate": patient.birth_date.strftime("%Y%m%d"),
        "PatientSex": sex,
        "StudyInstanceUID": study.study_instance_uid,
        "AccessionNumber": study.accession_number,
        "StudyDescription": study.description or study.body_site,
    }


def store_for_study(study, patient, data: bytes) -> dict[str, str]:  # noqa: ANN001
    """Store one image under an existing study and return the PACS ids.

    PNG and JPEG are converted to DICOM by Orthanc. Re-encoding the pixels
    also drops hidden metadata (EXIF: camera model, GPS position), which a
    photo taken with a phone can carry.

    A DICOM file from elsewhere carries someone else's patient and study
    identifiers. It is stored, rewritten with ours and stored again, and the
    original copy is deleted, so the PACS never keeps an image under an
    identity that is not in our record.
    """

    kind = validate_upload(data)
    tags = _dicom_tags(study, patient)

    if kind == "dicom":
        original = _request("POST", "/instances", content=data, headers={"Content-Type": "application/dicom"}).json()
        original_id = check_id(original["ID"])
        rewritten = _request(
            "POST",
            f"/instances/{original_id}/modify",
            json={"Replace": tags, "Force": True},
        ).content
        stored = _request("POST", "/instances", content=rewritten, headers={"Content-Type": "application/dicom"}).json()
        instance_id = check_id(stored["ID"])
        if instance_id != original_id:
            _request("DELETE", f"/instances/{original_id}")
    else:
        encoded = base64.b64encode(data).decode()
        created = _request(
            "POST",
            "/tools/create-dicom",
            json={
                "Tags": {**tags, "Modality": study.modality.value if hasattr(study.modality, "value") else study.modality,
                         "SeriesDescription": "Carga desde SaludRed"},
                "Content": f"data:image/{kind};base64,{encoded}",
                # Fijar StudyInstanceUID (el nuestro) exige Force en Orthanc 1.12:
                # sin el responde "Trying to override a value inherited from a
                # parent module".
                "Force": True,
            },
        ).json()
        instance_id = check_id(created["ID"])

    parent = _request("GET", f"/instances/{instance_id}/study").json()
    return {"instance_id": instance_id, "pacs_study_id": check_id(parent["ID"])}


def study_of_instance(instance_id: str) -> str:
    return check_id(_request("GET", f"/instances/{check_id(instance_id)}/study").json()["ID"])


def list_instances(pacs_study_id: str) -> list[dict[str, object]]:
    instances = _request("GET", f"/studies/{check_id(pacs_study_id)}/instances").json()
    result = []
    for item in instances:
        tags = item.get("MainDicomTags", {})
        result.append(
            {
                "instance_id": item["ID"],
                "number": int(tags["InstanceNumber"]) if str(tags.get("InstanceNumber", "")).isdigit() else None,
            }
        )
    result.sort(key=lambda r: (r["number"] is None, r["number"] or 0))
    return result


def preview(instance_id: str) -> bytes:
    return _request(
        "GET", f"/instances/{check_id(instance_id)}/preview", headers={"Accept": "image/png"}
    ).content
