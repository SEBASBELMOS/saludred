"""Relational model of the bed coordination network.

Importing this package registers every mapper on ``Base.metadata``, which is what
Alembic autogeneration reads. Any new model must be re-exported here or its table
will be silently missing from the next migration.
"""

from app.models.base import Base
from app.models.beds import BedAssignment, BedRequest, BedStatusEvent
from app.models.clinical import Encounter, Observation
from app.models.governance import AuditLog, FhirSyncLog, RecordVersion
from app.models.identity import Role, User
from app.models.organization import Location, Organization
from app.models.patient import Patient

__all__ = [
    "AuditLog",
    "Base",
    "BedAssignment",
    "BedRequest",
    "BedStatusEvent",
    "Encounter",
    "FhirSyncLog",
    "Location",
    "Observation",
    "Organization",
    "Patient",
    "RecordVersion",
    "Role",
    "User",
]
