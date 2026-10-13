"""Export and import of a clinical record as a FHIR R4 Bundle.

Export follows the access rules of the clinical record (``patient_access``):
staff inside their IPS, admin and coordinator network-wide, the patient only
their own. Import is for ``admin`` and ``medico``; it validates the whole Bundle
before writing anything, so a rejected Bundle leaves no trace but the 422.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.fhir import bundle as bundle_builder
from app.fhir.parsing import (
    FhirParseError,
    ParsedObservation,
    SubjectRef,
    parse_observation,
    parse_subject,
)
from app.models.clinical import Encounter, Observation
from app.models.enums import (
    AdministrativeGender,
    AuditAction,
    DocumentType,
    EncounterClass,
    EncounterStatus,
    ObservationOrigin,
    OrganizationType,
    Priority,
    RoleCode,
)
from app.models.identity import User
from app.models.organization import Organization
from app.models.patient import Patient
from app.services import ai_reports, clinical_record, patient_access, recipients, soft_ops, trail
from app.services.errors import ForbiddenError, InvalidValueError, commit

MAX_ENCOUNTERS = 200
MAX_IMPORT_OBSERVATIONS = 200
BUNDLE_TYPES = ("collection", "document")
_DOCUMENT_VALUE = re.compile(r"^(CC|TI|CE|PA|RC)[-\s]?([A-Za-z0-9]{4,32})$")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_bundle(db: Session, patient: Patient, user: User) -> dict[str, Any]:
    stmt = select(Encounter).where(
        Encounter.patient_id == patient.id, Encounter.deleted_at.is_(None)
    )
    scope = patient_access.scope_clause(
        user,
        organization_column=Encounter.organization_id,
        patient_column=Encounter.patient_id,
    )
    if scope is not None:
        stmt = stmt.where(scope)
    encounters = list(
        db.scalars(stmt.order_by(Encounter.started_at.desc()).limit(MAX_ENCOUNTERS))
    )
    observations = clinical_record._observations(db, patient.id, user)  # same scope rules
    reports = [
        clinical_record._report_item(r) for r in ai_reports.approved_reports(db, patient.id)
    ]
    return bundle_builder.clinical_bundle(
        patient,
        encounters,
        observations,
        reports,
        identifier_system=get_settings().fhir_identifier_system,
    )


def audit_export(db: Session, patient: Patient, bundle: dict[str, Any], user: User) -> None:
    kinds: dict[str, int] = {}
    for entry in bundle["entry"]:
        kind = entry["resource"]["resourceType"]
        kinds[kind] = kinds.get(kind, 0) + 1
    trail.audit(
        db,
        action=AuditAction.CLINICAL_RECORD_EXPORTED,
        entity_type=patient.__tablename__,
        entity_id=patient.id,
        actor=user,
        metadata={
            "patient_id": str(patient.id),
            "format": "fhir-bundle",
            "own_record": user.role.code == RoleCode.PATIENT,
            "resources": kinds,
        },
    )
    db.commit()


# ---------------------------------------------------------------------------
# Import: validation
# ---------------------------------------------------------------------------


@dataclass
class PatientData:
    document_type: DocumentType
    document_number: str
    first_name: str
    last_name: str
    birth_date: date
    gender: AdministrativeGender
    phone: str | None
    email: str | None
    address: str | None


@dataclass
class ObservationData:
    parsed: ParsedObservation
    key: str | None  # identifier in the source system, kept for provenance


@dataclass
class ValidatedBundle:
    patient: PatientData
    observations: list[ObservationData]
    skipped: dict[str, int]


def _parse_patient(resource: dict[str, Any], errors: list[str]) -> PatientData | None:
    system = get_settings().fhir_identifier_system
    doc_type: DocumentType | None = None
    number: str | None = None
    identifiers = [i for i in resource.get("identifier", []) or [] if isinstance(i, dict)]
    ordered = sorted(identifiers, key=lambda i: i.get("system") != f"{system}:documento")
    for identifier in ordered:
        match = _DOCUMENT_VALUE.match(str(identifier.get("value", "")).strip())
        if match:
            doc_type, number = DocumentType(match.group(1)), match.group(2)
            break
    if doc_type is None or number is None:
        errors.append(
            "Patient: falta un identifier con el documento (formato 'CC-123456', "
            f"sistema {system}:documento)"
        )

    names = resource.get("name") or []
    name = names[0] if names and isinstance(names[0], dict) else {}
    family = str(name.get("family") or "").strip()
    given = " ".join(str(g) for g in name.get("given", []) or []).strip()
    if not family or not given:
        errors.append("Patient: name necesita family y given")

    gender: AdministrativeGender | None = None
    try:
        gender = AdministrativeGender(resource.get("gender"))
    except ValueError:
        errors.append("Patient: gender debe ser male, female, other o unknown")

    birth: date | None = None
    try:
        birth = date.fromisoformat(str(resource.get("birthDate")))
    except ValueError:
        errors.append("Patient: birthDate es obligatoria (AAAA-MM-DD)")

    phone = email = None
    for telecom in resource.get("telecom", []) or []:
        if isinstance(telecom, dict) and telecom.get("system") == "phone" and not phone:
            phone = str(telecom.get("value", ""))[:40] or None
        if isinstance(telecom, dict) and telecom.get("system") == "email" and not email:
            email = str(telecom.get("value", ""))[:200] or None
    addresses = resource.get("address") or []
    address = None
    if addresses and isinstance(addresses[0], dict):
        address = str(addresses[0].get("text") or "")[:300] or None

    if errors or doc_type is None or number is None or gender is None or birth is None:
        return None
    return PatientData(doc_type, number, given[:120], family[:120], birth, gender, phone, email, address)


def _subject_matches(
    subject: SubjectRef | None, patient_full_url: str | None, patient: dict[str, Any], patient_data: PatientData
) -> bool:
    if subject is None:
        return False
    system = get_settings().fhir_identifier_system
    if subject.uuid_ref is not None:
        return subject.uuid_ref == patient_full_url
    if subject.fhir_id is not None:
        return subject.fhir_id == patient.get("id")
    if subject.identifier_value is not None:
        wanted = f"{patient_data.document_type.value}-{patient_data.document_number}"
        return (
            subject.identifier_system == f"{system}:documento" and subject.identifier_value == wanted
        ) or (
            subject.identifier_system == f"{system}:numero"
            and subject.identifier_value == patient_data.document_number
        )
    return False


def validate_bundle(bundle: Any) -> ValidatedBundle:
    """Strict validation. Raises ``InvalidValueError`` listing every problem found."""

    errors: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise InvalidValueError("El cuerpo debe ser un Bundle FHIR (resourceType: Bundle)")
    if bundle.get("type") not in BUNDLE_TYPES:
        errors.append(f"Bundle.type debe ser uno de {', '.join(BUNDLE_TYPES)}")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        raise InvalidValueError("; ".join([*errors, "Bundle.entry esta vacio"]))

    patients: list[tuple[str | None, dict[str, Any]]] = []
    observations: list[tuple[int, dict[str, Any]]] = []
    skipped: dict[str, int] = {}
    for index, entry in enumerate(entries):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or not isinstance(resource.get("resourceType"), str):
            errors.append(f"entry[{index}]: no trae un recurso FHIR")
            continue
        kind = resource["resourceType"]
        if kind == "Patient":
            patients.append((entry.get("fullUrl"), resource))
        elif kind == "Observation":
            observations.append((index, resource))
        else:
            skipped[kind] = skipped.get(kind, 0) + 1

    if len(patients) != 1:
        errors.append(f"El Bundle debe traer exactamente un Patient (trae {len(patients)})")
    if not observations:
        errors.append("El Bundle debe traer al menos una Observation")
    if len(observations) > MAX_IMPORT_OBSERVATIONS:
        errors.append(f"Maximo {MAX_IMPORT_OBSERVATIONS} Observation por Bundle")
    if len(patients) != 1:
        raise InvalidValueError("; ".join(errors))

    patient_url, patient_resource = patients[0]
    patient_data = _parse_patient(patient_resource, errors)

    system = get_settings().fhir_identifier_system
    parsed_observations: list[ObservationData] = []
    for index, resource in observations[:MAX_IMPORT_OBSERVATIONS]:
        try:
            parsed = parse_observation(resource)
        except FhirParseError as exc:
            errors.append(f"entry[{index}] Observation: {exc}")
            continue
        if not parsed.has_value:
            errors.append(f"entry[{index}] Observation: no trae un valor reconocible")
        if patient_data is not None and not _subject_matches(
            parse_subject(resource), patient_url, patient_resource, patient_data
        ):
            errors.append(f"entry[{index}] Observation: subject no apunta al Patient del Bundle")
        key = None
        for identifier in resource.get("identifier", []) or []:
            if isinstance(identifier, dict) and identifier.get("system") == f"{system}:observations":
                key = str(identifier.get("value"))[:120]
        key = key or (str(resource["id"])[:120] if resource.get("id") else None)
        parsed_observations.append(ObservationData(parsed, key))

    if errors or patient_data is None:
        raise InvalidValueError("; ".join(errors[:12]))
    return ValidatedBundle(patient_data, parsed_observations, skipped)


# ---------------------------------------------------------------------------
# Import: write
# ---------------------------------------------------------------------------


def _resolve_eps(db: Session, actor: User, eps_organization_id: uuid.UUID | None) -> Organization:
    candidate = eps_organization_id
    if candidate is None and actor.organization_id is not None:
        own = db.get(Organization, actor.organization_id)
        candidate = own.parent_organization_id if own is not None else None
    eps = db.get(Organization, candidate) if candidate else None
    if eps is None or eps.organization_type != OrganizationType.EPS or eps.deleted_at is not None:
        raise InvalidValueError(
            "El paciente es nuevo: indique eps_organization_id con la EPS a la que se afilia"
        )
    return eps


def _target_organization(
    db: Session, actor: User, patient: Patient | None, organization_id: uuid.UUID | None
) -> uuid.UUID:
    if actor.role.code != RoleCode.ADMIN:
        if organization_id is not None and organization_id != actor.organization_id:
            raise ForbiddenError("Solo puede importar hacia su propia IPS")
        if actor.organization_id is None:
            raise ForbiddenError("La cuenta no pertenece a ninguna IPS")
        return actor.organization_id
    if organization_id is not None:
        ips = db.get(Organization, organization_id)
        if ips is None or ips.organization_type != OrganizationType.IPS:
            raise InvalidValueError("organization_id debe ser una IPS existente")
        return organization_id
    if patient is not None:
        latest = db.scalar(recipients.latest_encounter_organization_stmt(patient.id))
        if latest is not None:
            return latest
    raise InvalidValueError("Indique organization_id (la IPS que recibe la importacion)")


def import_bundle(
    db: Session,
    bundle: Any,
    *,
    actor: User,
    eps_organization_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    validated = validate_bundle(bundle)
    data = validated.patient

    patient = db.scalar(
        select(Patient).where(
            Patient.document_type == data.document_type,
            Patient.document_number == data.document_number,
            Patient.deleted_at.is_(None),
        )
    )
    created_patient = patient is None
    if patient is not None and actor.role.code != RoleCode.ADMIN:
        # A medico may enrich only patients treated at their own IPS.
        patient_access.ensure_staff_patient_scope(db, actor, patient.id)

    target_org = _target_organization(db, actor, patient, organization_id)

    if patient is None:
        eps = _resolve_eps(db, actor, eps_organization_id)
        patient = Patient(
            document_type=data.document_type,
            document_number=data.document_number,
            first_name=data.first_name,
            last_name=data.last_name,
            birth_date=data.birth_date,
            gender=data.gender,
            phone=data.phone,
            email=data.email,
            address=data.address,
            eps_organization_id=eps.id,
            created_by=actor.id,
            responsible_clinician_id=actor.id
            if actor.role.code in recipients.CLINICIAN_ROLES
            else None,
        )
        db.add(patient)
        db.flush()
        soft_ops.audit_create(db, patient, actor=actor)

    encounter = _encounter_in(db, patient.id, target_org)
    times = [o.parsed.observed_at for o in validated.observations]
    if encounter is None:
        started, ended = min(times), max(times)
        encounter = Encounter(
            patient_id=patient.id,
            organization_id=target_org,
            encounter_class=EncounterClass.AMB,
            status=EncounterStatus.FINISHED,
            priority=Priority.ROUTINE,
            reason_text="Importacion de historia clinica (FHIR)",
            started_at=started,
            ended_at=max(started, ended),
            created_by=actor.id,
        )
        db.add(encounter)
        db.flush()
        soft_ops.audit_create(db, encounter, actor=actor)

    created = duplicates = 0
    for item in validated.observations:
        parsed = item.parsed
        if _observation_exists(db, patient.id, parsed):
            duplicates += 1
            continue
        db.add(
            Observation(
                patient_id=patient.id,
                encounter_id=encounter.id,
                status=parsed.status,
                code_system=parsed.code_system,
                code=parsed.code,
                display=parsed.display,
                value_numeric=parsed.value_numeric,
                value_text=parsed.value_text,
                unit=parsed.unit,
                observed_at=parsed.observed_at,
                origin=ObservationOrigin.IMPORT,
                fhir_resource_id=item.key,
                created_by=actor.id,
            )
        )
        created += 1

    summary = {
        "paciente_id": str(patient.id),
        "paciente_creado": created_patient,
        "encuentro_id": str(encounter.id),
        "observaciones_creadas": created,
        "observaciones_duplicadas": duplicates,
        "recursos_omitidos": validated.skipped,
    }
    trail.audit(
        db,
        action=AuditAction.FHIR_IMPORT,
        entity_type=patient.__tablename__,
        entity_id=patient.id,
        actor=actor,
        metadata={"origin": "import", "patient_id": str(patient.id), **summary},
    )
    commit(db)
    return summary


def _encounter_in(db: Session, patient_id: uuid.UUID, organization_id: uuid.UUID) -> Encounter | None:
    base = select(Encounter).where(
        Encounter.patient_id == patient_id,
        Encounter.organization_id == organization_id,
        Encounter.deleted_at.is_(None),
    )
    open_one = db.scalar(
        base.where(
            Encounter.ended_at.is_(None),
            Encounter.status.in_((EncounterStatus.IN_PROGRESS, EncounterStatus.ARRIVED)),
        )
        .order_by(Encounter.started_at.desc())
        .limit(1)
    )
    if open_one is not None:
        return open_one
    return db.scalar(base.order_by(Encounter.started_at.desc()).limit(1))


def _observation_exists(db: Session, patient_id: uuid.UUID, parsed: ParsedObservation) -> bool:
    stmt = select(Observation.id).where(
        Observation.patient_id == patient_id,
        Observation.code == parsed.code,
        Observation.observed_at == parsed.observed_at,
        Observation.deleted_at.is_(None),
    )
    if parsed.value_numeric is not None:
        stmt = stmt.where(Observation.value_numeric == parsed.value_numeric)
    else:
        stmt = stmt.where(Observation.value_text == parsed.value_text)
    return db.scalar(stmt.limit(1)) is not None

