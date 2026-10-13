"""Shared fakes for the FHIR/event tests: no test opens a socket."""

from __future__ import annotations

import copy
import itertools
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.models import Encounter, FhirSyncLog, Patient
from app.services import fhir_sync
from app.services.errors import FhirGatewayError

HOOK = "/api/v1/fhir/hooks"


def hook_headers(secret: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {secret or get_settings().fhir_hook_secret}",
        "Content-Type": "application/fhir+json",
    }


class FakeFhir:
    """In-memory stand-in for HAPI, with the same surface as ``FhirClient``."""

    def __init__(self) -> None:
        self.down = False
        self.store: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    def _check(self) -> None:
        if self.down:
            raise FhirGatewayError("test: FHIR server down")

    def conditional_update(self, resource_type, identifier_system, identifier_value, payload):  # noqa: ANN001
        self._check()
        self.calls.append(("PUT", resource_type))
        bucket = self.store.setdefault(resource_type, {})
        for fhir_id, stored in bucket.items():
            if any(
                i.get("system") == identifier_system and i.get("value") == identifier_value
                for i in stored.get("identifier", [])
            ):
                bucket[fhir_id] = {**copy.deepcopy(payload), "id": fhir_id}
                return fhir_id, "2"
        fhir_id = f"{resource_type[:3].lower()}-{next(self._ids)}"
        bucket[fhir_id] = {**copy.deepcopy(payload), "id": fhir_id}
        return fhir_id, "1"

    def search(self, resource_type, params):  # noqa: ANN001
        self._check()
        tag = params.get("_tag")
        out = []
        for stored in self.store.get(resource_type, {}).values():
            tags = [f"{t['system']}|{t['code']}" for t in stored.get("meta", {}).get("tag", [])]
            if tag is None or tag in tags:
                out.append(stored)
        return out

    def create(self, resource_type, payload):  # noqa: ANN001
        self._check()
        self.calls.append(("POST", resource_type))
        fhir_id = f"{resource_type[:3].lower()}-{next(self._ids)}"
        self.store.setdefault(resource_type, {})[fhir_id] = {
            **copy.deepcopy(payload), "id": fhir_id, "status": "active",
        }
        return self.store[resource_type][fhir_id]

    def update(self, resource_type, fhir_id, payload):  # noqa: ANN001
        self._check()
        self.calls.append(("PUT", resource_type))
        self.store.setdefault(resource_type, {})[fhir_id] = {
            **copy.deepcopy(payload), "id": fhir_id, "status": "active",
        }
        return self.store[resource_type][fhir_id]

    def read(self, resource_type, fhir_id):  # noqa: ANN001
        self._check()
        return self.store[resource_type][fhir_id]

    def find_by_identifier(self, resource_type: str, value: str) -> list[dict[str, Any]]:
        """What ``GET [base]/Type?identifier=value`` would answer (any system)."""

        return [
            r
            for r in self.store.get(resource_type, {}).values()
            if any(i.get("value") == value for i in r.get("identifier", []))
        ]


def glucose(
    patient_ref: str,
    *,
    obs_id: str = "obs-100",
    version: str | None = "1",
    value: float = 182,
    tags: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """The Observation the course tester posts: glucose, LOINC 2339-0."""

    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "id": obs_id,
        "status": "final",
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "2339-0", "display": "Glucose [Mass/volume] in Blood"}
            ]
        },
        "subject": {"reference": patient_ref},
        "effectiveDateTime": "2026-10-03T10:00:00Z",
        "valueQuantity": {
            "value": value, "unit": "mg/dL",
            "system": "http://unitsofmeasure.org", "code": "mg/dL",
        },
    }
    if version is not None:
        resource["meta"] = {"versionId": version}
    if tags:
        resource.setdefault("meta", {})["tag"] = tags
    return resource


def sync_patient_to_fake(db, fake: FakeFhir, patient_id, actor) -> str:  # noqa: ANN001
    """Push a patient to the fake HAPI and return its server id."""

    patient = db.get(Patient, patient_id)
    record = fhir_sync.sync_patient(db, fake, patient, actor=actor)
    return record.fhir_resource_id


def open_encounter(db, patient_id, organization_id, actor) -> Encounter:  # noqa: ANN001
    from app.models.enums import EncounterClass, EncounterStatus, Priority

    encounter = Encounter(
        patient_id=patient_id,
        organization_id=organization_id,
        encounter_class=EncounterClass.EMER,
        status=EncounterStatus.IN_PROGRESS,
        priority=Priority.URGENT,
        started_at=datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc),
        created_by=actor.id,
    )
    db.add(encounter)
    db.commit()
    return encounter


def sync_record(db, patient_id):  # noqa: ANN001
    return db.query(FhirSyncLog).filter_by(entity_type="patients", entity_id=patient_id).one_or_none()
