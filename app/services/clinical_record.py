"""Clinical record export: JSON (R10) and PDF (R11).

The record holds the patient's data, the observations and the AI reports that a
clinician APPROVED. Pending and rejected reports are never part of it -- they
live in ``ai_reports`` and in the audit trail, not in the legal record
(Res. 1995 de 1999: the record is the clinician's document).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import AiReport
from app.models.clinical import Encounter, Observation
from app.models.enums import AuditAction, RoleCode
from app.models.identity import User
from app.models.patient import Patient
from app.schemas.ai import ClinicalRecordRead, ClinicalRecordReport
from app.schemas.observation import ObservationRead
from app.schemas.patient import PatientRead
from app.services import ai_reports, patient_access, trail

MAX_OBSERVATIONS = 500


def _observations(db: Session, patient_id: uuid.UUID, user: User) -> list[Observation]:
    stmt = select(Observation).where(
        Observation.patient_id == patient_id, Observation.deleted_at.is_(None)
    )
    scope = patient_access.scope_clause(
        user,
        organization_column=Encounter.organization_id,
        patient_column=Observation.patient_id,
    )
    if scope is not None:
        stmt = stmt.join(Encounter, Observation.encounter_id == Encounter.id).where(scope)
    return list(db.scalars(stmt.order_by(Observation.observed_at.desc()).limit(MAX_OBSERVATIONS)))


def _report_item(report: AiReport) -> ClinicalRecordReport:
    read = ai_reports.to_read(report)
    return ClinicalRecordReport(
        id=read.id,
        kind=read.kind,
        estado=read.estado,
        probability=read.probability,
        nivel=read.nivel,
        model_version=read.model_version,
        top_features=read.top_features,
        observaciones=read.observaciones,
        firmado_por=read.firmado_por,
        reviewed_at=read.reviewed_at,
        created_at=read.created_at,
    )


def build_record(db: Session, patient: Patient, user: User) -> ClinicalRecordRead:
    return ClinicalRecordRead(
        paciente=PatientRead.model_validate(patient),
        observaciones=[
            ObservationRead.model_validate(o) for o in _observations(db, patient.id, user)
        ],
        reportes_ia=[_report_item(r) for r in ai_reports.approved_reports(db, patient.id)],
        generado_en=datetime.now(timezone.utc),
    )


def audit_view(db: Session, patient: Patient, record: ClinicalRecordRead, user: User) -> None:
    trail.audit(
        db,
        action=AuditAction.RECORD_VIEWED,
        entity_type="patients",
        entity_id=patient.id,
        actor=user,
        metadata={
            "patient_id": str(patient.id),
            "format": "json",
            "approved_report_ids": [str(r.id) for r in record.reportes_ia],
        },
    )
    db.commit()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _latin(text: str | None) -> str:
    """Core PDF fonts are Latin-1; anything else degrades to '?', never crashes."""

    return (text or "").encode("latin-1", "replace").decode("latin-1")


def _fmt(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def render_pdf(record: ClinicalRecordRead) -> bytes:
    patient = record.paciente
    pdf = FPDF(format="A4")
    # Uncompressed on purpose: the text stays readable to any PDF text
    # extractor, and the file is a few KB.
    pdf.set_compression(False)
    pdf.set_title("Historia clinica")
    pdf.set_author("SaludRed")
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    def heading(text: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)

    def line(text: str) -> None:
        pdf.multi_cell(0, 5.5, _latin(text), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Historia clinica", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Generada: {_fmt(record.generado_en)} UTC", new_x="LMARGIN", new_y="NEXT")

    heading("Datos del paciente")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _latin(f"{patient.first_name} {patient.last_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    line(f"Documento: {patient.document_type.value} {patient.document_number}")
    line(f"Fecha de nacimiento: {patient.birth_date.isoformat()}    Sexo: {patient.gender.value}")
    if patient.phone:
        line(f"Telefono: {patient.phone}")
    if patient.address:
        line(f"Direccion: {patient.address}")

    heading(f"Observaciones ({len(record.observaciones)})")
    if not record.observaciones:
        line("Sin observaciones registradas.")
    for obs in record.observaciones:
        value = obs.value_numeric if obs.value_numeric is not None else obs.value_text
        unit = f" {obs.unit}" if obs.unit and obs.value_numeric is not None else ""
        line(f"{_fmt(obs.observed_at)}  {obs.display}: {value}{unit}  (LOINC {obs.code})")

    heading(f"Reportes de IA aprobados ({len(record.reportes_ia)})")
    if not record.reportes_ia:
        line("Sin reportes de IA aprobados.")
    for item in record.reportes_ia:
        pdf.set_font("Helvetica", "B", 10)
        line(f"Reporte {item.id}")
        pdf.set_font("Helvetica", "", 10)
        line(
            f"Resultado: {item.nivel}  |  Probabilidad: {item.probability:.1%}  |  "
            f"Modelo: {item.model_version}"
        )
        drivers = ", ".join(f"{f.feature} ({f.contribution:+.2f})" for f in item.top_features)
        if drivers:
            line(f"Variables que mas pesaron: {drivers}")
        line(f"Firmado por: {item.firmado_por or '-'}  el {_fmt(item.reviewed_at)}")
        if item.observaciones:
            line(f"Concepto clinico: {item.observaciones}")
        pdf.ln(2)

    pdf.ln(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0,
        4,
        _latin(
            "Los reportes de IA son apoyo a la decision; solo se incluyen los que un "
            "medico o especialista reviso y aprobo."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    return bytes(pdf.output())


def audit_export(db: Session, patient: Patient, record: ClinicalRecordRead, user: User) -> None:
    trail.audit(
        db,
        action=AuditAction.CLINICAL_RECORD_EXPORTED,
        entity_type="patients",
        entity_id=patient.id,
        actor=user,
        metadata={
            "patient_id": str(patient.id),
            "format": "pdf",
            "own_record": user.role.code == RoleCode.PATIENT,
            "approved_report_ids": [str(r.id) for r in record.reportes_ia],
        },
    )
    db.commit()
