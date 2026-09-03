"""Self-service endpoints for the PATIENT role.

A patient account never browses the administrative API: its entire contract is
"my own record". The link is ``users.patient_id``, set when the account is
created, so what a patient can read is decided by a foreign key -- not by a
filter the client is trusted to send.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.enums import RoleCode
from app.models.patient import Patient
from app.schemas.encounter import EncounterRead
from app.schemas.observation import ObservationRead
from app.schemas.patient import PatientRead
from app.services import encounters as encounters_service
from app.services import observations as observations_service
from app.services.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/api/v1/me", tags=["mi informacion"])


def _own_patient(db: DbSession, user: CurrentUser) -> Patient:
    authz.require_role(user, RoleCode.PATIENT)
    if user.patient_id is None:
        raise ConflictError("La cuenta no esta vinculada a ningun paciente")
    patient = db.get(Patient, user.patient_id)
    if patient is None or patient.deleted_at is not None:
        raise NotFoundError("Paciente no encontrado")
    return patient


@router.get("/patient", response_model=PatientRead, summary="Mi ficha de paciente")
def my_patient(db: DbSession, user: CurrentUser) -> PatientRead:
    return _own_patient(db, user)


@router.get(
    "/encounters", response_model=list[EncounterRead], summary="Mis encuentros"
)
def my_encounters(db: DbSession, user: CurrentUser) -> list[EncounterRead]:
    patient = _own_patient(db, user)
    return encounters_service.list_encounters_for_patient(db, patient.id)


@router.get(
    "/observations", response_model=list[ObservationRead], summary="Mis observaciones"
)
def my_observations(db: DbSession, user: CurrentUser) -> list[ObservationRead]:
    patient = _own_patient(db, user)
    items, _total = observations_service.list_observations(
        db, page=1, page_size=100, patient_id=patient.id
    )
    return items
