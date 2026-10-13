"""Clinical record export (R10, R11).

Quien puede leerla: roles institucionales dentro de su IPS y el propio paciente
(solo la suya). El contable recibe 403. Contiene unicamente reportes de IA
aprobados; descargarla queda en la auditoria.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession
from app.schemas.ai import ClinicalRecordRead
from app.services import clinical_record, fhir_exchange, patient_access
from app.services import patients as patients_service

router = APIRouter(prefix="/api/v1/patients", tags=["historia clinica"])


@router.get(
    "/{patient_id}/clinical-record",
    response_model=ClinicalRecordRead,
    summary="Historia clinica en JSON (solo reportes de IA aprobados)",
)
def get_clinical_record(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> ClinicalRecordRead:
    patient_access.ensure_record_access(db, user, patient_id)
    patient = patients_service.get_patient(db, patient_id)
    record = clinical_record.build_record(db, patient, user)
    clinical_record.audit_view(db, patient, record, user)
    return record


@router.get(
    "/{patient_id}/clinical-record.pdf",
    summary="Historia clinica en PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def get_clinical_record_pdf(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> Response:
    patient_access.ensure_record_access(db, user, patient_id)
    patient = patients_service.get_patient(db, patient_id)
    record = clinical_record.build_record(db, patient, user)
    content = clinical_record.render_pdf(record)
    clinical_record.audit_export(db, patient, record, user)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="historia-clinica-{patient.document_number}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/{patient_id}/fhir-bundle",
    summary="Historia clinica como Bundle FHIR R4 (collection)",
    response_class=JSONResponse,
    responses={200: {"content": {"application/fhir+json": {}}}},
)
def get_fhir_bundle(
    db: DbSession, user: CurrentUser, patient_id: uuid.UUID
) -> JSONResponse:
    """Patient, encounters, observations and approved AI reports (RiskAssessment).
    Same access rules as the clinical record; the export is audited."""

    patient_access.ensure_record_access(db, user, patient_id)
    patient = patients_service.get_patient(db, patient_id)
    bundle = fhir_exchange.export_bundle(db, patient, user)
    fhir_exchange.audit_export(db, patient, bundle, user)
    return JSONResponse(
        content=bundle,
        media_type="application/fhir+json",
        headers={"Cache-Control": "private, no-store"},
    )
