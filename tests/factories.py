"""In-memory ORM instances for unit tests.

Instances are built directly (never flushed), so column defaults that only
apply at INSERT time are passed explicitly here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.clinical import Encounter, Observation
from app.models.enums import (
    AdministrativeGender,
    BedStatus,
    DocumentType,
    EncounterClass,
    EncounterStatus,
    LocationType,
    ObservationStatus,
    OrganizationType,
    Priority,
    RoleCode,
)
from app.models.identity import Role, User
from app.models.organization import Location, Organization
from app.models.patient import Patient

IDENTIFIER_SYSTEM = "urn:saludred:identifier"


def make_user(
    role_code: RoleCode, *, organization_id: uuid.UUID | None = None
) -> User:
    return User(
        id=uuid.uuid4(),
        username=f"user-{role_code.value.lower()}",
        full_name="Test User",
        password_hash="not-a-hash",
        role=Role(code=role_code, name=role_code.value, description="test"),
        organization_id=organization_id,
    )


def make_patient(**overrides) -> Patient:
    values = dict(
        id=uuid.uuid4(),
        document_type=DocumentType.CC,
        document_number="1032456789",
        first_name="Maria Camila",
        last_name="Ramirez",
        birth_date=date(1990, 5, 17),
        gender=AdministrativeGender.FEMALE,
        phone="3004567890",
        email="maria@example.org",
        address="Calle 10 # 20-30",
        eps_organization_id=uuid.uuid4(),
        deleted_at=None,
    )
    values.update(overrides)
    return Patient(**values)


def make_encounter(**overrides) -> Encounter:
    values = dict(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        encounter_class=EncounterClass.IMP,
        status=EncounterStatus.IN_PROGRESS,
        priority=Priority.URGENT,
        reason_text="Dolor abdominal agudo",
        started_at=datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc),
        ended_at=None,
        deleted_at=None,
    )
    values.update(overrides)
    return Encounter(**values)


def make_observation(**overrides) -> Observation:
    values = dict(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        encounter_id=uuid.uuid4(),
        status=ObservationStatus.FINAL,
        code_system="http://loinc.org",
        code="8867-4",
        display="Heart rate",
        value_numeric=Decimal("82.0"),
        value_text=None,
        unit="/min",
        unit_system="http://unitsofmeasure.org",
        observed_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        deleted_at=None,
    )
    values.update(overrides)
    return Observation(**values)


def make_organization(**overrides) -> Organization:
    values = dict(
        id=uuid.uuid4(),
        code="IPS-NORTE",
        name="Clinica Norte",
        organization_type=OrganizationType.IPS,
        parent_organization_id=uuid.uuid4(),
        deleted_at=None,
    )
    values.update(overrides)
    return Organization(**values)


def make_location(**overrides) -> Location:
    values = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        parent_location_id=None,
        location_type=LocationType.BED,
        code="IPS-NORTE-S1-H1-C1",
        name="Cama 101-1",
        service="Hospitalizacion",
        status=BedStatus.AVAILABLE,
        deleted_at=None,
    )
    values.update(overrides)
    return Location(**values)
