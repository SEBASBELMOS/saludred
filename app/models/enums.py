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
    """The four roles of the system.

    ADMIN, IPS_CLINICAL_OPERATOR and PATIENT cover the three archetypes required
    by the assignment. EPS_COORDINATOR is the domain-specific role that makes the
    multi-IPS network scope meaningful.
    """

    ADMIN = "ADMIN"
    EPS_COORDINATOR = "EPS_COORDINATOR"
    IPS_CLINICAL_OPERATOR = "IPS_CLINICAL_OPERATOR"
    PATIENT = "PATIENT"


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


class AuditAction(str, Enum):
    """Actions recorded in audit_log. The last three are required by the spec."""

    LOGIN = "LOGIN"
    CREATE = "CREATE"
    SOFT_EDIT = "SOFT_EDIT"
    SOFT_DELETE = "SOFT_DELETE"
    RESTORE = "RESTORE"
    FHIR_SYNC = "FHIR_SYNC"


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
