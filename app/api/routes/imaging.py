"""Imaging study REST endpoints.

The browser never talks to the PACS. Every route here goes through the token
and the role check first, so that an image can only be reached by someone the
system has already authorised -- and so that the access leaves a trace. A PACS
does not know our roles and does not write to our audit log; putting it behind
the API is what makes both possible.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, DbSession, PageQuery
from app.core import authz
from app.models.enums import ImagingModality, RoleCode
from app.models.imaging import ImagingStudy
from app.schemas.audit import RecordVersionRead
from app.schemas.common import Page
from app.schemas.imaging import (
    ImagingStudyCreate,
    ImagingStudyRead,
    ImagingStudyUpdate,
)
from app.services import imaging as imaging_service
from app.services import pacs, patient_access
from app.services import patients as patients_service
from app.services import soft_ops
from app.services.errors import InvalidUploadError, NotFoundError

router = APIRouter(prefix="/api/v1", tags=["imagenes"])


@router.get(
    "/imaging-studies",
    response_model=Page[ImagingStudyRead],
    summary="Listar estudios de imagen",
)
def list_studies(
    db: DbSession,
    user: CurrentUser,
    params: PageQuery,
    patient_id: uuid.UUID | None = Query(default=None),
    encounter_id: uuid.UUID | None = Query(default=None),
    modality: ImagingModality | None = Query(default=None),
) -> Page[ImagingStudyRead]:
    authz.require_staff(user)
    items, total = imaging_service.list_studies(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        encounter_id=encounter_id,
        modality=modality,
        organization_id=authz.org_filter_for(user),
        granted_patients=patient_access.referred_patient_ids(user),
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post(
    "/imaging-studies",
    response_model=ImagingStudyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar estudio de imagen",
)
def create_study(
    db: DbSession, user: CurrentUser, payload: ImagingStudyCreate
) -> ImagingStudyRead:
    authz.require_role(user, *authz.CLINICAL_WRITERS)
    authz.ensure_org_scope(user, payload.organization_id)
    patients_service.get_patient(db, payload.patient_id)
    return imaging_service.create_study(db, payload, actor=user)


@router.get(
    "/imaging-studies/{study_id}",
    response_model=ImagingStudyRead,
    summary="Consultar estudio de imagen",
)
def get_study(
    db: DbSession, user: CurrentUser, study_id: uuid.UUID
) -> ImagingStudyRead:
    authz.require_staff(user)
    study = imaging_service.get_study(db, study_id)
    patient_access.ensure_scope_or_referral(db, user, study.organization_id, study.patient_id)
    return study


@router.put(
    "/imaging-studies/{study_id}",
    response_model=ImagingStudyRead,
    summary="Editar estudio de imagen",
)
def update_study(
    db: DbSession,
    user: CurrentUser,
    study_id: uuid.UUID,
    payload: ImagingStudyUpdate,
) -> ImagingStudyRead:
    authz.require_role(user, *authz.CLINICAL_WRITERS)
    study = imaging_service.get_study(db, study_id)
    authz.ensure_org_scope(user, study.organization_id)
    return imaging_service.update_study(db, study, payload, actor=user)


@router.delete(
    "/imaging-studies/{study_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar estudio de imagen (borrado logico)",
)
def delete_study(db: DbSession, user: CurrentUser, study_id: uuid.UUID) -> Response:
    study = imaging_service.get_study(db, study_id)
    authz.ensure_org_scope(user, study.organization_id)
    authz.ensure_owner_or_admin(user, study.created_by)
    imaging_service.soft_delete_study(db, study, actor=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/imaging-studies/{study_id}/restore",
    response_model=ImagingStudyRead,
    summary="Restaurar estudio de imagen eliminado",
)
def restore_study(
    db: DbSession, user: CurrentUser, study_id: uuid.UUID
) -> ImagingStudyRead:
    authz.require_admin(user)
    study = soft_ops.get_including_deleted(db, ImagingStudy, study_id)
    soft_ops.restore(db, study, actor=user)
    db.refresh(study)
    return study


@router.get(
    "/imaging-studies/{study_id}/history",
    response_model=list[RecordVersionRead],
    summary="Historial de versiones del estudio",
)
def study_history(
    db: DbSession, user: CurrentUser, study_id: uuid.UUID
) -> list[RecordVersionRead]:
    authz.require_staff(user)
    study = soft_ops.get_including_deleted(db, ImagingStudy, study_id)
    authz.ensure_org_scope(user, study.organization_id)
    return soft_ops.list_history(db, "imaging_studies", study_id)


@router.get(
    "/patients/{patient_id}/imaging-studies",
    response_model=Page[ImagingStudyRead],
    summary="Estudios de imagen de un paciente",
)
def patient_studies(
    db: DbSession, user: CurrentUser, params: PageQuery, patient_id: uuid.UUID
) -> Page[ImagingStudyRead]:
    """Staff view. A patient reads their own studies through ``/me``.

    The institutional filter goes into the query rather than into a list
    comprehension here: rows belonging to another IPS must never leave the
    database, not merely be dropped after arriving.
    """

    authz.require_staff(user)
    patients_service.get_patient(db, patient_id)
    items, total = imaging_service.list_studies(
        db,
        page=params.page,
        page_size=params.page_size,
        patient_id=patient_id,
        organization_id=authz.org_filter_for(user),
        granted_patients=patient_access.referred_patient_ids(user),
    )
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.get(
    "/patients/{patient_id}/imagenes",
    summary="Imagenes de un paciente (forma del contrato de verificacion)",
)
def patient_images(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    patient_id: uuid.UUID,
) -> dict[str, list[dict[str, object]]]:
    """Downloadable images of a patient, in the contract's shape.

    Each element is an image in the PACS, not a study: ``id`` is the instance id
    and ``url`` the path that downloads it (``GET /imaging/instances/{id}/preview``,
    a PNG, same bearer token). ``url_absoluta`` adds the public base of the request.
    Scope is that of the patient's studies: own IPS plus patients referred to a
    specialist; whatever is out of scope is simply not listed. A patient account
    lists only its own images (another patient's id answers 404). The list is
    not paginated: it covers the patient's most recent ``IMAGES_MAX_STUDIES``
    (100) studies. If the PACS is down the answer is 503 with a message in
    Spanish, never a 500; a failure on a single study just skips that study.
    """

    if user.role.code == RoleCode.PATIENT:
        if user.patient_id != patient_id:
            raise NotFoundError("Paciente no encontrado")
        studies, _total = imaging_service.list_studies(
            db, page=1, page_size=IMAGES_MAX_STUDIES, patient_id=patient_id
        )
    else:
        authz.require_staff(user)
        patients_service.get_patient(db, patient_id)
        studies, _total = imaging_service.list_studies(
            db,
            page=1,
            page_size=IMAGES_MAX_STUDIES,
            patient_id=patient_id,
            organization_id=authz.org_filter_for(user),
            granted_patients=patient_access.referred_patient_ids(user),
        )
    return {"imagenes": imaging_service.downloadable_images(studies, base_url=_public_base(request))}


IMAGES_MAX_STUDIES = 100  # same cap as the front (frontend/src/api/imaging.ts)
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def _public_base(request: Request) -> str | None:
    """Scheme and host the client used, for ``url_absoluta``.

    Both come from headers a client (or a proxy) can set, so each is checked:
    only ``http``/``https`` is accepted for the scheme (else the server's own),
    and the host must be a plain ``host[:port]``; otherwise there is no absolute
    variant (``None``) and the relative ``url`` is all the response offers.
    """

    scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = request.url.scheme
    host = (request.headers.get("host") or "").strip()
    if not _HOST.match(host):
        return None
    return f"{scheme}://{host}"


# ---------------------------------------------------------------------------
# Pixels in the PACS (draft, pending confirmation with the course)
# ---------------------------------------------------------------------------


def _ensure_can_view(db, user, study: ImagingStudy) -> None:  # noqa: ANN001
    """Who may see an image. Refusals answer 404, not 403.

    Answering "forbidden" would confirm that the study exists; for the most
    sensitive part of a record, not even that is disclosed. A specialist with a
    live referral for the patient may see the patient's images at any IPS.
    """

    code = user.role.code
    if code == RoleCode.PATIENT:
        if user.patient_id != study.patient_id:
            raise NotFoundError("Estudio de imagen no encontrado")
        return
    if (
        code in authz.IPS_SCOPED_ROLES
        and user.organization_id != study.organization_id
        and not patient_access.has_referral_access(db, user, study.patient_id)
    ):
        raise NotFoundError("Estudio de imagen no encontrado")
    if code not in authz.STAFF_ROLES:
        raise NotFoundError("Estudio de imagen no encontrado")


def _store(db, user, study_id: uuid.UUID, data: bytes) -> dict:  # noqa: ANN001
    authz.require_role(user, *authz.CLINICAL_WRITERS)
    study = imaging_service.get_study(db, study_id)
    authz.ensure_org_scope(user, study.organization_id)
    patient = patients_service.get_patient(db, study.patient_id)
    stored = pacs.store_for_study(study, patient, data)
    imaging_service.attach_pacs_study(db, study, stored["pacs_study_id"], actor=user)
    return {**stored, "images": len(pacs.list_instances(stored["pacs_study_id"]))}


@router.post(
    "/imaging-studies/{study_id}/images",
    status_code=status.HTTP_201_CREATED,
    summary="Subir una imagen al PACS para un estudio registrado (borrador)",
)
async def upload_image(
    request: Request, db: DbSession, user: CurrentUser, study_id: uuid.UUID
) -> dict:
    """El cuerpo es el archivo tal cual: DICOM, PNG o JPEG.

    El tipo se decide por la firma de los bytes, no por el nombre ni por el
    Content-Type declarado. Maximo 15 MB y 4096 px por lado.
    """

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > pacs.MAX_UPLOAD_BYTES:
        raise InvalidUploadError("El archivo supera el maximo de 15 MB")
    data = await request.body()
    return await run_in_threadpool(_store, db, user, study_id, data)


@router.get(
    "/imaging-studies/{study_id}/images",
    summary="Imagenes de un estudio en el PACS (borrador)",
)
def study_images(db: DbSession, user: CurrentUser, study_id: uuid.UUID) -> dict:
    study = imaging_service.get_study(db, study_id)
    _ensure_can_view(db, user, study)
    if not study.pacs_study_id:
        return {"study_id": str(study.id), "images": []}
    return {"study_id": str(study.id), "images": pacs.list_instances(study.pacs_study_id)}


@router.get(
    "/imaging/instances/{instance_id}/preview",
    summary="Vista previa PNG de una imagen (borrador)",
    response_class=Response,
)
def image_preview(db: DbSession, user: CurrentUser, instance_id: str) -> Response:
    """La imagen se sirve solo despues de ubicar su estudio en nuestra base y
    aplicar la misma regla de acceso que a la ficha del paciente."""

    study = imaging_service.find_by_pacs_study(db, pacs.study_of_instance(instance_id))
    _ensure_can_view(db, user, study)
    return Response(
        content=pacs.preview(instance_id),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )
