"""Bed cleaning evidence endpoints.

Autorizacion, en resumen:

- Registrar la limpieza y subir la foto: el operador clinico de la IPS de la
  cama (la foto, solo quien registro o un administrador) y el administrador.
- Aprobar o rechazar: coordinador de EPS o administrador, nunca quien registro.
- Consultar registros y foto: roles institucionales, dentro de su IPS.
- Camas sin limpieza aprobada: coordinador de EPS y administrador.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.cleaning import BedCleaningRecord
from app.models.enums import RoleCode
from app.models.organization import Location
from app.schemas.cleaning import (
    CleaningRecordCreate,
    CleaningRecordRead,
    CleaningReview,
    OverdueCleaningBed,
)
from app.services import cleaning as cleaning_service
from app.services.errors import InvalidUploadError, NotFoundError

router = APIRouter(prefix="/api/v1", tags=["limpieza"])

CREATE_ROLES = (RoleCode.ADMIN, RoleCode.IPS_CLINICAL_OPERATOR)
REVIEW_ROLES = (RoleCode.ADMIN, RoleCode.EPS_COORDINATOR)


def _bed_or_404(db: DbSession, bed_id: uuid.UUID) -> Location:
    cama = db.get(Location, bed_id)
    if cama is None or cama.deleted_at is not None:
        raise NotFoundError("Cama no encontrada")
    return cama


def _record_in_scope(
    db: DbSession, user: CurrentUser, record_id: uuid.UUID
) -> BedCleaningRecord:
    record = cleaning_service.get_record(db, record_id)
    authz.ensure_org_scope(user, record.location.organization_id)
    return record


@router.get(
    "/beds/cleaning/overdue",
    response_model=list[OverdueCleaningBed],
    summary="Camas libres pero sin limpieza aprobada",
)
def overdue_cleaning(
    db: DbSession,
    user: CurrentUser,
    minutes: int = Query(
        default=60, ge=0, description="Minutos minimos en limpieza sin evidencia aprobada"
    ),
) -> list[OverdueCleaningBed]:
    """Base de la alerta critica "cama libre pero no limpiada"."""

    authz.require_role(user, *REVIEW_ROLES)
    return cleaning_service.beds_cleaning_overdue(db, minutes)


@router.post(
    "/beds/{bed_id}/cleaning",
    response_model=CleaningRecordRead,
    status_code=201,
    summary="Registrar la limpieza terminal de una cama",
)
def create_cleaning_record(
    db: DbSession, user: CurrentUser, bed_id: uuid.UUID, payload: CleaningRecordCreate
) -> CleaningRecordRead:
    """La cama debe estar en CLEANING y la lista de chequeo completa.

    Items de la lista (CDC, Environmental Checklist for Monitoring Terminal
    Cleaning): barandas, colchon, mesa_auxiliar, timbre_y_controles,
    poste_de_suero, manijas_e_interruptores, cortinas, bano, piso, residuos,
    ropa_de_cama. Con aislamiento se exige cambio de cortinas; con esporas,
    ademas, desinfectante esporicida. El registro nace en PENDING_REVIEW.
    """

    authz.require_role(user, *CREATE_ROLES)
    cama = _bed_or_404(db, bed_id)
    authz.ensure_org_scope(user, cama.organization_id)
    return cleaning_service.create_record(db, cama, payload, actor=user)


@router.get(
    "/beds/{bed_id}/cleaning",
    response_model=list[CleaningRecordRead],
    summary="Registros de limpieza de una cama",
)
def list_cleaning_records(
    db: DbSession, user: CurrentUser, bed_id: uuid.UUID
) -> list[CleaningRecordRead]:
    authz.require_staff(user)
    cama = _bed_or_404(db, bed_id)
    authz.ensure_org_scope(user, cama.organization_id)
    return cleaning_service.list_for_bed(db, cama.id)


@router.put(
    "/cleaning/{record_id}/photo",
    response_model=CleaningRecordRead,
    summary="Subir la foto de evidencia de una limpieza",
)
async def upload_cleaning_photo(
    request: Request, db: DbSession, user: CurrentUser, record_id: uuid.UUID
) -> CleaningRecordRead:
    """El cuerpo es la imagen tal cual: PNG o JPEG, maximo 5 MB y 4096 px.

    El tipo se decide por la firma de los bytes. Solo quien registro la
    limpieza (o un administrador) y solo mientras esta en revision.
    """

    authz.require_role(user, *CREATE_ROLES)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cleaning_service.MAX_PHOTO_BYTES:
        raise InvalidUploadError("El archivo supera el maximo de 5 MB")
    data = await request.body()
    return await run_in_threadpool(_attach_photo, db, user, record_id, data)


def _attach_photo(
    db: DbSession, user: CurrentUser, record_id: uuid.UUID, data: bytes
) -> BedCleaningRecord:
    record = _record_in_scope(db, user, record_id)
    authz.ensure_owner_or_admin(user, record.created_by)
    return cleaning_service.attach_photo(db, record, data, actor=user)


@router.get(
    "/cleaning/{record_id}/photo",
    summary="Foto de evidencia de una limpieza",
    response_class=Response,
)
def get_cleaning_photo(db: DbSession, user: CurrentUser, record_id: uuid.UUID) -> Response:
    authz.require_staff(user)
    record = _record_in_scope(db, user, record_id)
    content, content_type = cleaning_service.get_photo(db, record)
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.post(
    "/cleaning/{record_id}/review",
    response_model=CleaningRecordRead,
    summary="Aprobar o rechazar un registro de limpieza",
)
def review_cleaning_record(
    db: DbSession, user: CurrentUser, record_id: uuid.UUID, payload: CleaningReview
) -> CleaningRecordRead:
    """Quien registro la limpieza no puede aprobarla (403). Aprobar exige foto
    (409); rechazar exige `review_notes` (422)."""

    authz.require_role(user, *REVIEW_ROLES)
    record = _record_in_scope(db, user, record_id)
    return cleaning_service.review_record(
        db,
        record,
        approved=payload.approved,
        review_notes=payload.review_notes,
        actor=user,
    )
