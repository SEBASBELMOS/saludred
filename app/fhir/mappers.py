"""Relational rows -> FHIR R4 resources.

Pure functions: they take ORM entities plus already-resolved references and
return JSON-safe dicts, touching neither the database nor the network. That is
what makes them unit-testable without a FHIR server.

References are passed in as strings (``"Patient/123"``) rather than looked up
here, because knowing the id a resource received on the server is the
synchronization service's job -- and its dependency ordering guarantees the
referenced resource exists before anything pointing at it is sent.

Every code system URI and code below comes from the FHIR R4 specification;
none is invented. Elements with cardinality 1..1 (``Encounter.status``,
``Encounter.class``, ``Observation.status``, ``Observation.code``,
``Location.status``) are always emitted -- omitting them makes the server
reject the resource.
"""

from __future__ import annotations

from typing import Any

from app.models.clinical import Encounter, Observation
from app.models.enums import (
    BedStatus,
    EncounterClass,
    LocationType,
    OrganizationType,
    Priority,
    ReferralStatus,
)
from app.models.organization import Location, Organization
from app.models.patient import Patient
from app.models.referral import Referral

V3_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
V3_ACT_PRIORITY = "http://terminology.hl7.org/CodeSystem/v3-ActPriority"
V2_0116 = "http://terminology.hl7.org/CodeSystem/v2-0116"
LOCATION_PHYSICAL_TYPE = "http://terminology.hl7.org/CodeSystem/location-physical-type"
ORGANIZATION_TYPE = "http://terminology.hl7.org/CodeSystem/organization-type"
OBSERVATION_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
LOINC = "http://loinc.org"

ENCOUNTER_CLASS_DISPLAY = {
    EncounterClass.IMP: "inpatient encounter",
    EncounterClass.AMB: "ambulatory",
    EncounterClass.EMER: "emergency",
}

PRIORITY_TO_ACT_PRIORITY = {
    Priority.ROUTINE: ("R", "routine"),
    Priority.URGENT: ("UR", "urgent"),
    Priority.EMERGENCY: ("EM", "emergency"),
}

# The standard mechanism for a bed's operational state. RESERVED collapses into
# Occupied (not assignable) and MAINTENANCE into Closed: v2-0116 has no closer
# codes, and inventing them would break interoperability instead of adding it.
BED_STATUS_TO_OPERATIONAL = {
    BedStatus.AVAILABLE: ("U", "Unoccupied"),
    BedStatus.OCCUPIED: ("O", "Occupied"),
    BedStatus.RESERVED: ("O", "Occupied"),
    BedStatus.CLEANING: ("H", "Housekeeping"),
    BedStatus.BLOCKED: ("C", "Closed"),
    BedStatus.MAINTENANCE: ("C", "Closed"),
}

LOCATION_TYPE_TO_PHYSICAL = {
    LocationType.FACILITY: ("si", "Site"),
    LocationType.WARD: ("wa", "Ward"),
    LocationType.ROOM: ("ro", "Room"),
    LocationType.BED: ("bd", "Bed"),
}

# The EPS pays for care, the IPS provides it.
ORGANIZATION_TYPE_CODE = {
    OrganizationType.EPS: ("pay", "Payer"),
    OrganizationType.IPS: ("prov", "Healthcare Provider"),
}


def _identifier(system: str, value: str) -> list[dict[str, str]]:
    return [{"system": system, "value": value}]


def origin_tag_system(identifier_system: str) -> str:
    return f"{identifier_system}:origin"


ORIGIN_TAG_CODE = "saludred"


def with_origin_tag(resource: dict[str, Any], identifier_system: str) -> dict[str, Any]:
    """Mark a resource as pushed by SaludRed.

    The Subscription fires for EVERY Observation HAPI stores, including the ones
    this system pushed itself (R15). The tag is how the rest-hook recognizes its
    own echo and ignores it instead of notifying the clinician twice. HAPI keeps
    ``meta.tag`` on stored resources and returns it in the delivery.
    """

    meta = resource.setdefault("meta", {})
    meta.setdefault("tag", []).append(
        {
            "system": origin_tag_system(identifier_system),
            "code": ORIGIN_TAG_CODE,
            "display": "Pushed by SaludRed",
        }
    )
    return resource


def is_own_resource(resource: dict[str, Any], identifier_system: str) -> bool:
    """True when the resource carries SaludRed's origin tag.

    A resource whose identifier uses one of this project's systems also counts:
    a client that dropped the tag is still recognizably ours.
    """

    system = origin_tag_system(identifier_system)
    for tag in resource.get("meta", {}).get("tag", []) or []:
        if tag.get("system") == system and tag.get("code") == ORIGIN_TAG_CODE:
            return True
    prefix = f"{identifier_system}:"
    for identifier in resource.get("identifier", []) or []:
        if str(identifier.get("system", "")).startswith(prefix):
            return True
    return False


def patient_to_fhir(
    patient: Patient,
    *,
    identifier_system: str,
    managing_organization_ref: str | None = None,
) -> dict[str, Any]:
    """Build a FHIR Patient.

    The identifier carries the national document, not the internal UUID: it is
    the value the FHIR server uses to recognize "this same person" across
    repeated synchronizations, which is the root of idempotency.
    """

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        # Three identifiers, so the patient is findable in HAPI by whichever key
        # a client knows: ``?identifier=CC-123`` (type + number, the sync key),
        # ``?identifier=123`` (the bare number) or the API's own patient id.
        "identifier": [
            {
                "system": f"{identifier_system}:documento",
                "value": patient.business_identifier,
            },
            {
                "system": f"{identifier_system}:numero",
                "value": patient.document_number,
            },
            {"system": f"{identifier_system}:patient-id", "value": str(patient.id)},
        ],
        "active": patient.deleted_at is None,
        "name": [
            {
                "family": patient.last_name,
                "given": patient.first_name.split(),
            }
        ],
        "gender": patient.gender.value,
        "birthDate": patient.birth_date.isoformat(),
    }

    telecom: list[dict[str, str]] = []
    if patient.phone:
        telecom.append({"system": "phone", "value": patient.phone})
    if patient.email:
        telecom.append({"system": "email", "value": patient.email})
    if telecom:
        resource["telecom"] = telecom
    if patient.address:
        resource["address"] = [{"text": patient.address}]
    if managing_organization_ref:
        resource["managingOrganization"] = {"reference": managing_organization_ref}
    return with_origin_tag(resource, identifier_system)


def encounter_to_fhir(
    encounter: Encounter,
    *,
    identifier_system: str,
    patient_ref: str,
    service_provider_ref: str | None = None,
    location_ref: str | None = None,
) -> dict[str, Any]:
    """Build a FHIR Encounter.

    ``location`` is where the bed-management domain meets the standard: the bed
    occupied during the encounter travels as ``Encounter.location``.
    """

    class_code = encounter.encounter_class
    priority_code, priority_display = PRIORITY_TO_ACT_PRIORITY[encounter.priority]

    resource: dict[str, Any] = {
        "resourceType": "Encounter",
        "identifier": _identifier(
            f"{identifier_system}:encounters", str(encounter.id)
        ),
        "status": encounter.status.value,
        "class": {
            "system": V3_ACT_CODE,
            "code": class_code.value,
            "display": ENCOUNTER_CLASS_DISPLAY[class_code],
        },
        "priority": {
            "coding": [
                {
                    "system": V3_ACT_PRIORITY,
                    "code": priority_code,
                    "display": priority_display,
                }
            ]
        },
        "subject": {"reference": patient_ref},
        "period": {"start": encounter.started_at.isoformat()},
    }
    if encounter.ended_at is not None:
        resource["period"]["end"] = encounter.ended_at.isoformat()
    if encounter.reason_text:
        resource["reasonCode"] = [{"text": encounter.reason_text}]
    if service_provider_ref:
        resource["serviceProvider"] = {"reference": service_provider_ref}
    if location_ref:
        resource["location"] = [{"location": {"reference": location_ref}}]
    return with_origin_tag(resource, identifier_system)


def observation_to_fhir(
    observation: Observation,
    *,
    identifier_system: str,
    patient_ref: str,
    encounter_ref: str,
) -> dict[str, Any]:
    """Build a FHIR Observation with its LOINC code and UCUM unit."""

    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "identifier": _identifier(
            f"{identifier_system}:observations", str(observation.id)
        ),
        "status": observation.status.value,
        "code": {
            "coding": [
                {
                    "system": observation.code_system,
                    "code": observation.code,
                    "display": observation.display,
                }
            ],
            "text": observation.display,
        },
        "subject": {"reference": patient_ref},
        "encounter": {"reference": encounter_ref},
        "effectiveDateTime": observation.observed_at.isoformat(),
    }
    if observation.code_system == LOINC:
        resource["category"] = [
            {
                "coding": [
                    {
                        "system": OBSERVATION_CATEGORY,
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ]
    if observation.value_numeric is not None:
        resource["valueQuantity"] = {
            "value": float(observation.value_numeric),
            "unit": observation.unit,
            "system": observation.unit_system,
            "code": observation.unit,
        }
    else:
        resource["valueString"] = observation.value_text
    return with_origin_tag(resource, identifier_system)


def organization_to_fhir(
    organization: Organization,
    *,
    identifier_system: str,
    part_of_ref: str | None = None,
) -> dict[str, Any]:
    """Build a FHIR Organization.

    ``partOf`` is the standard element for institutional hierarchy: it is what
    expresses EPS -> IPS in FHIR itself, not just in our schema.
    """

    type_code, type_display = ORGANIZATION_TYPE_CODE[organization.organization_type]
    resource: dict[str, Any] = {
        "resourceType": "Organization",
        "identifier": _identifier(
            f"{identifier_system}:organizations", organization.code
        ),
        "active": organization.deleted_at is None,
        "name": organization.name,
        "type": [
            {
                "coding": [
                    {
                        "system": ORGANIZATION_TYPE,
                        "code": type_code,
                        "display": type_display,
                    }
                ]
            }
        ],
    }
    if part_of_ref:
        resource["partOf"] = {"reference": part_of_ref}
    return with_origin_tag(resource, identifier_system)


def location_to_fhir(
    location: Location,
    *,
    identifier_system: str,
    managing_organization_ref: str,
    part_of_ref: str | None = None,
) -> dict[str, Any]:
    """Build a FHIR Location.

    For a bed, ``operationalStatus`` (HL7 table v2-0116) carries the state the
    whole project revolves around: free, occupied, being cleaned, closed.
    """

    physical_code, physical_display = LOCATION_TYPE_TO_PHYSICAL[location.location_type]
    resource: dict[str, Any] = {
        "resourceType": "Location",
        "identifier": _identifier(f"{identifier_system}:locations", location.code),
        "status": "active" if location.deleted_at is None else "inactive",
        "name": location.name,
        "mode": "instance",
        "physicalType": {
            "coding": [
                {
                    "system": LOCATION_PHYSICAL_TYPE,
                    "code": physical_code,
                    "display": physical_display,
                }
            ]
        },
        "managingOrganization": {"reference": managing_organization_ref},
    }
    if location.status is not None:
        op_code, op_display = BED_STATUS_TO_OPERATIONAL[location.status]
        resource["operationalStatus"] = {
            "system": V2_0116,
            "code": op_code,
            "display": op_display,
        }
    if location.service:
        resource["description"] = f"Servicio: {location.service}"
    if part_of_ref:
        resource["partOf"] = {"reference": part_of_ref}
    return with_origin_tag(resource, identifier_system)


# A referral is a request for a service: FHIR R4 ``ServiceRequest``. Pending and
# accepted referrals are still open (``active``); a completed one is
# ``completed``; a rejected one never happened (``revoked``).
REFERRAL_STATUS_TO_FHIR = {
    ReferralStatus.PENDING: "active",
    ReferralStatus.ACCEPTED: "active",
    ReferralStatus.COMPLETED: "completed",
    ReferralStatus.REJECTED: "revoked",
}

SNOMED = "http://snomed.info/sct"


def _practitioner_reference(
    identifier_system: str, user_id: object, display: str
) -> dict[str, Any]:
    """A logical reference to a clinician: a display name and our own identifier."""

    return {
        "identifier": {"system": f"{identifier_system}:users", "value": str(user_id)},
        "display": display,
    }


def referral_to_fhir(
    referral: Referral,
    *,
    identifier_system: str,
    patient_ref: str,
    requester_name: str,
    performer_name: str,
) -> dict[str, Any]:
    """Build a FHIR ServiceRequest for a referral.

    ``subject`` is the patient's FHIR reference, ``requester`` the referring
    doctor and ``performer`` the specialist; ``reasonCode.text`` carries the
    reason. ``intent`` is ``order``: a doctor asks another to see the patient.
    """

    resource: dict[str, Any] = {
        "resourceType": "ServiceRequest",
        "identifier": _identifier(f"{identifier_system}:referrals", str(referral.id)),
        "status": REFERRAL_STATUS_TO_FHIR[referral.status],
        "intent": "order",
        "code": {
            "coding": [
                {"system": SNOMED, "code": "3457005", "display": "Patient referral"}
            ],
            "text": "Remision a especialista",
        },
        "subject": {"reference": patient_ref},
        "requester": _practitioner_reference(
            identifier_system, referral.referring_user_id, requester_name
        ),
        "performer": [
            _practitioner_reference(
                identifier_system, referral.specialist_user_id, performer_name
            )
        ],
        "reasonCode": [{"text": referral.motivo}],
    }
    if referral.created_at is not None:
        resource["authoredOn"] = referral.created_at.isoformat()
    return with_origin_tag(resource, identifier_system)
