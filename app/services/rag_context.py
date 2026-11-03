"""Short pseudonymized clinical summary of one patient, for the RAG prompt.

Built from the local database and scoped like any read of the record (an
institution-bound clinician only gets what it could already read). Only three
things go in, each with its own date so the model can reason about recency:

* the reason of the latest encounter,
* the latest value of each recent observation, with its unit,
* the AI criticality reports a clinician approved.

The patient's name, document, phone, e-mail and address are never read into the
summary; free text (encounter reason, text observations) is scrubbed anyway
because a clinician may have typed a name in it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clinical import Encounter, Observation
from app.models.enums import ObservationStatus
from app.models.identity import User
from app.models.patient import Patient
from app.services import ai_reports, patient_access, pseudonymization
from app.services.rag import privacy

MAX_OBSERVATIONS = 8
MAX_REPORTS = 3


@dataclass(frozen=True)
class PatientContext:
    pseudonym: str
    text: str
    identity: privacy.PatientIdentity

    @property
    def is_empty(self) -> bool:
        return not self.text


def _format_value(observation: Observation) -> str:
    if observation.value_numeric is not None:
        value = f"{float(observation.value_numeric):g}"
        return f"{value} {observation.unit}" if observation.unit else value
    return observation.value_text or ""


def _latest_encounter(db: Session, patient_id: uuid.UUID, org: uuid.UUID | None) -> Encounter | None:
    stmt = select(Encounter).where(Encounter.patient_id == patient_id, Encounter.deleted_at.is_(None))
    if org is not None:
        stmt = stmt.where(Encounter.organization_id == org)
    return db.scalar(stmt.order_by(Encounter.started_at.desc()).limit(1))


def _latest_observations(
    db: Session, patient_id: uuid.UUID, org: uuid.UUID | None
) -> list[Observation]:
    stmt = select(Observation).where(
        Observation.patient_id == patient_id,
        Observation.deleted_at.is_(None),
        Observation.status != ObservationStatus.CANCELLED,
    )
    if org is not None:
        stmt = stmt.join(Encounter, Observation.encounter_id == Encounter.id).where(
            Encounter.organization_id == org
        )
    latest: dict[str, Observation] = {}
    for observation in db.scalars(stmt.order_by(Observation.observed_at.desc())):
        latest.setdefault(observation.code, observation)
        if len(latest) >= MAX_OBSERVATIONS:
            break
    return list(latest.values())


def build_patient_context(db: Session, user: User, patient: Patient) -> PatientContext:
    pseudonym = pseudonymization.pseudonym_for(patient.id)
    identity = privacy.PatientIdentity.of(patient, pseudonym)
    org = patient_access.org_scope_for_patient(db, user, patient.id)

    lines: list[str] = []
    encounter = _latest_encounter(db, patient.id, org)
    if encounter is not None and encounter.reason_text:
        lines.append(
            f"Motivo de la última atención ({encounter.started_at:%Y-%m-%d}): "
            f"{encounter.reason_text.strip()}"
        )
    observations = _latest_observations(db, patient.id, org)
    if observations:
        lines.append("Últimos valores registrados:")
        lines.extend(
            f"- {o.display}: {_format_value(o)} ({o.observed_at:%Y-%m-%d %H:%M})" for o in observations
        )
    reports = ai_reports.approved_reports(db, patient.id)[:MAX_REPORTS]
    for report in reports:
        when = report.reviewed_at or report.created_at
        lines.append(
            f"Reporte de IA aprobado ({when:%Y-%m-%d}): criticidad {report.level}, "
            f"probabilidad {float(report.probability):.2f}."
        )
    text = privacy.scrub("\n".join(lines), identity)
    return PatientContext(pseudonym=pseudonym, text=text, identity=identity)
