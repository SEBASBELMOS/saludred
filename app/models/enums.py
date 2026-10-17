"""Controlled vocabularies for the relational model.

Values that cross the FHIR boundary are stored using the exact codes defined by
HL7 FHIR R4, so the integration mapper never needs a translation table. This is
deliberate: every translation layer is a place where the two models can silently
drift apart.
"""

from __future__ import annotations

from enum import Enum


class OrganizationType(str, Enum):
    """EPS is the insurer coordinating the network, IPS is the care provider."""

    EPS = "EPS"
    IPS = "IPS"


class LocationType(str, Enum):
    """Physical hierarchy inside an IPS. Maps to Location.physicalType."""

    FACILITY = "FACILITY"
    WARD = "WARD"
    ROOM = "ROOM"
    BED = "BED"


class BedStatus(str, Enum):
    """Operational state of a bed. Maps to Location.operationalStatus."""

    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    CLEANING = "CLEANING"
    BLOCKED = "BLOCKED"
    MAINTENANCE = "MAINTENANCE"


class DocumentType(str, Enum):
    """Colombian identity document types."""

    CC = "CC"  # Cedula de ciudadania
    TI = "TI"  # Tarjeta de identidad
    CE = "CE"  # Cedula de extranjeria
    PA = "PA"  # Pasaporte
    RC = "RC"  # Registro civil


class AdministrativeGender(str, Enum):
    """FHIR R4 administrative-gender value set. Stored verbatim."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class RoleCode(str, Enum):
    """The six roles of the system.

    ADMIN, IPS_CLINICAL_OPERATOR ("medico"), SPECIALIST, PATIENT and ACCOUNTANT
    cover the roles required by the assignment. EPS_COORDINATOR is the
    domain-specific role that makes the multi-IPS network scope meaningful.
    SPECIALIST is clinical staff of one IPS; ACCOUNTANT belongs to an EPS,
    handles billing and never sees clinical data.
    """

    ADMIN = "ADMIN"
    EPS_COORDINATOR = "EPS_COORDINATOR"
    IPS_CLINICAL_OPERATOR = "IPS_CLINICAL_OPERATOR"
    PATIENT = "PATIENT"
    SPECIALIST = "SPECIALIST"
    ACCOUNTANT = "ACCOUNTANT"


class EncounterClass(str, Enum):
    """FHIR R4 v3-ActCode subset used by Encounter.class (required, 1..1)."""

    IMP = "IMP"  # Inpatient encounter
    AMB = "AMB"  # Ambulatory
    EMER = "EMER"  # Emergency


class EncounterStatus(str, Enum):
    """FHIR R4 encounter-status value set (required, 1..1)."""

    PLANNED = "planned"
    ARRIVED = "arrived"
    IN_PROGRESS = "in-progress"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    EMERGENCY = "EMERGENCY"


class ObservationStatus(str, Enum):
    """FHIR R4 observation-status value set (required, 1..1)."""

    REGISTERED = "registered"
    PRELIMINARY = "preliminary"
    FINAL = "final"
    AMENDED = "amended"
    CANCELLED = "cancelled"


class BedRequestStatus(str, Enum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    ASSIGNED = "ASSIGNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class BedAssignmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class ImagingModality(str, Enum):
    """DICOM modality codes.

    Los valores son los del estandar DICOM, no nombres propios: un visor o un
    PACS de otro proveedor los reconoce sin traduccion.
    """

    CR = "CR"  # Radiografia computarizada
    DX = "DX"  # Radiografia digital
    CT = "CT"  # Tomografia computarizada
    MR = "MR"  # Resonancia magnetica
    US = "US"  # Ecografia
    XA = "XA"  # Angiografia
    NM = "NM"  # Medicina nuclear


class ImagingStudyStatus(str, Enum):
    """FHIR R4 imagingstudy-status value set."""

    REGISTERED = "registered"
    AVAILABLE = "available"
    CANCELLED = "cancelled"


class ClinicalProfile(str, Enum):
    """Perfil clinico estable de un paciente sintetico.

    No es un diagnostico: es la etiqueta que usa el generador para producir
    signos vitales coherentes entre si, y la variable que despues permite
    agrupar pacientes y comparar cohortes.
    """

    HEALTHY = "HEALTHY"
    HYPERTENSIVE = "HYPERTENSIVE"
    DIABETIC = "DIABETIC"
    CARDIAC = "CARDIAC"
    RESPIRATORY = "RESPIRATORY"
    ELDERLY_FRAIL = "ELDERLY_FRAIL"


class AuditAction(str, Enum):
    """Actions recorded in audit_log.

    The first block covers authentication and record lifecycle. The rest are
    reserved for the AI, referral, billing, alert, FHIR and imaging flows, so
    every sensitive action can leave a trace with a stable name.
    """

    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ACCOUNT_UNLOCKED = "ACCOUNT_UNLOCKED"
    CREATE = "CREATE"
    SOFT_EDIT = "SOFT_EDIT"
    SOFT_DELETE = "SOFT_DELETE"
    RESTORE = "RESTORE"
    FHIR_SYNC = "FHIR_SYNC"
    AI_ANALYSIS = "AI_ANALYSIS"
    REPORT_APPROVED = "REPORT_APPROVED"
    REPORT_REJECTED = "REPORT_REJECTED"
    REFERRAL_CREATED = "REFERRAL_CREATED"
    REFERRAL_ACCEPTED = "REFERRAL_ACCEPTED"
    REFERRAL_REJECTED = "REFERRAL_REJECTED"
    REFERRAL_COMPLETED = "REFERRAL_COMPLETED"
    INVOICE_CREATED = "INVOICE_CREATED"
    INVOICE_PAID = "INVOICE_PAID"
    INVOICE_VOIDED = "INVOICE_VOIDED"
    ALERT_CREATED = "ALERT_CREATED"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
    ALERT_ESCALATED = "ALERT_ESCALATED"
    FHIR_EVENT_RECEIVED = "FHIR_EVENT_RECEIVED"
    CLEANING_EVIDENCE = "CLEANING_EVIDENCE"
    RECORD_VIEWED = "RECORD_VIEWED"
    CLINICAL_RECORD_EXPORTED = "CLINICAL_RECORD_EXPORTED"
    FHIR_IMPORT = "FHIR_IMPORT"
    RESPONSIBLE_CHANGED = "RESPONSIBLE_CHANGED"


class CleaningIsolation(str, Enum):
    """Isolation precautions in force for the room being cleaned."""

    NONE = "NONE"
    CONTACT = "CONTACT"
    DROPLET = "DROPLET"
    SPORES = "SPORES"


class CleaningDisinfectant(str, Enum):
    CHLORINE_5000PPM = "CHLORINE_5000PPM"
    QUATERNARY_AMMONIUM = "QUATERNARY_AMMONIUM"
    SPORICIDAL = "SPORICIDAL"
    OTHER = "OTHER"


class CleaningReviewStatus(str, Enum):
    """A cleaning record is evidence only once a person other than its author approves it."""

    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AiReportKind(str, Enum):
    """What an AI report is about. Only criticality exists today."""

    CRITICALITY = "criticality"


class AiReportOrigin(str, Enum):
    """How an AI report came to exist: the system on its own, or a clinician's request."""

    AUTOMATIC = "automatico"
    MANUAL = "manual"


class AiReportStatus(str, Enum):
    """Clinical sign-off state of an AI report (R10, "soft put").

    Values are the Spanish ones of the public contract. Only an APPROVED report
    reaches the clinical record; a REJECTED one is kept as evidence.
    """

    PENDING = "pendiente"
    APPROVED = "aprobado"
    REJECTED = "rechazado"


class ReferralStatus(str, Enum):
    """Life cycle of a referral (R08), values of the public Spanish contract.

    ``pendiente`` -> ``aceptada`` -> ``completada``; the specialist may also
    answer ``rechazada`` while it is still pending. Pending, accepted and
    completed referrals grant the specialist access to the patient's record; a
    rejected one grants nothing.
    """

    PENDING = "pendiente"
    ACCEPTED = "aceptada"
    COMPLETED = "completada"
    REJECTED = "rechazada"


class AlertKind(str, Enum):
    """What raised an alert (R20)."""

    CRITICAL_PATIENT = "paciente_critico"
    BED_NOT_CLEANED = "cama_sin_limpiar"


class AlertStatus(str, Enum):
    """An alert stays ``activa`` until somebody acknowledges it (or, for beds,
    until the cleaning is approved)."""

    ACTIVE = "activa"
    ACKNOWLEDGED = "reconocida"


class CohortOrigin(str, Enum):
    """Where a triage row of the cohort comes from."""

    REAL = "REAL"
    SYNTHETIC = "SYNTHETIC"


class CohortZone(str, Enum):
    """Distance of a cluster to the critical case, ranked by admission rate."""

    FAR = "FAR"
    INTERMEDIATE = "INTERMEDIATE"
    NEAR = "NEAR"


class ObservationOrigin(str, Enum):
    """Where an observation row comes from.

    ``local`` rows are entered in SaludRed and pushed to HAPI. ``fhir`` rows
    arrived through the Subscription rest-hook and must never be pushed back.
    ``import`` rows came from a FHIR Bundle uploaded by a user.
    """

    LOCAL = "local"
    FHIR = "fhir"
    IMPORT = "import"


class FhirInboundOutcome(str, Enum):
    """What the system did with a resource delivered by the FHIR server."""

    PROCESSED = "processed"
    UNKNOWN_PATIENT = "unknown_patient"


class SyncStatus(str, Enum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# SQLAlchemy helper
# ---------------------------------------------------------------------------

from sqlalchemy import Enum as SAEnum  # noqa: E402


def enum_column(python_enum: type[Enum], constraint_name: str) -> SAEnum:
    """Build a VARCHAR column backed by a CHECK constraint.

    Native PostgreSQL ENUM types were avoided on purpose: altering them from
    Alembic requires hand-written DDL, and this schema is expected to keep moving.
    A VARCHAR with a CHECK constraint gives the same guarantee, shows up directly
    in the generated DDL, and stays trivial to migrate.

    ``values_callable`` forces the enum *value* to be persisted rather than the
    member name, which is what keeps FHIR codes such as ``in-progress`` intact.
    """

    return SAEnum(
        python_enum,
        name=constraint_name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        length=32,
    )
