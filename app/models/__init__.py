"""Relational model of the bed coordination network.

Importing this package registers every mapper on ``Base.metadata``, which is what
Alembic autogeneration reads. Any new model must be re-exported here or its table
will be silently missing from the next migration.
"""

from app.models.ai import AiReport
from app.models.alert import Alert
from app.models.base import Base
from app.models.beds import BedAssignment, BedRequest, BedStatusEvent
from app.models.cleaning import BedCleaningRecord
from app.models.clinical import Encounter, Observation
from app.models.cohort import CohortCluster, CohortRecord
from app.models.governance import AuditLog, FhirInboundEvent, FhirSyncLog, RecordVersion
from app.models.identity import Role, User
from app.models.imaging import ImagingStudy
from app.models.notification import Notification
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.models.referral import Referral

__all__ = [
    "AiReport",
    "Alert",
    "AuditLog",
    "Base",
    "BedAssignment",
    "BedCleaningRecord",
    "BedRequest",
    "BedStatusEvent",
    "CohortCluster",
    "CohortRecord",
    "Encounter",
    "FhirInboundEvent",
    "FhirSyncLog",
    "ImagingStudy",
    "Location",
    "Notification",
    "Observation",
    "Organization",
    "Patient",
    "RecordVersion",
    "Referral",
    "Role",
    "User",
]
