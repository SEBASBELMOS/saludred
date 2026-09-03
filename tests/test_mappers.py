"""Mapper contract: relational rows must become valid, complete R4 resources."""

from __future__ import annotations


from app.fhir import mappers
from app.models.enums import BedStatus, EncounterStatus, LocationType, OrganizationType
from tests.factories import (
    IDENTIFIER_SYSTEM,
    make_encounter,
    make_location,
    make_observation,
    make_organization,
    make_patient,
)


class TestPatientMapper:
    def test_identifier_uses_national_document(self):
        resource = mappers.patient_to_fhir(
            make_patient(), identifier_system=IDENTIFIER_SYSTEM
        )
        assert resource["resourceType"] == "Patient"
        assert resource["identifier"] == [
            {"system": f"{IDENTIFIER_SYSTEM}:documento", "value": "CC-1032456789"}
        ]

    def test_demographics(self):
        resource = mappers.patient_to_fhir(
            make_patient(), identifier_system=IDENTIFIER_SYSTEM
        )
        assert resource["gender"] == "female"
        assert resource["birthDate"] == "1990-05-17"
        assert resource["name"] == [
            {"family": "Ramirez", "given": ["Maria", "Camila"]}
        ]
        assert {"system": "phone", "value": "3004567890"} in resource["telecom"]

    def test_managing_organization_reference(self):
        resource = mappers.patient_to_fhir(
            make_patient(),
            identifier_system=IDENTIFIER_SYSTEM,
            managing_organization_ref="Organization/eps-1",
        )
        assert resource["managingOrganization"] == {"reference": "Organization/eps-1"}

    def test_optional_contact_fields_absent_when_empty(self):
        patient = make_patient(phone=None, email=None, address=None)
        resource = mappers.patient_to_fhir(
            patient, identifier_system=IDENTIFIER_SYSTEM
        )
        assert "telecom" not in resource
        assert "address" not in resource


class TestEncounterMapper:
    def test_required_elements_always_present(self):
        # status and class are 1..1 in R4: omitting them is a rejected resource.
        resource = mappers.encounter_to_fhir(
            make_encounter(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
        )
        assert resource["status"] == "in-progress"
        assert resource["class"]["system"] == mappers.V3_ACT_CODE
        assert resource["class"]["code"] == "IMP"

    def test_period_end_only_when_closed(self):
        open_resource = mappers.encounter_to_fhir(
            make_encounter(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
        )
        assert "end" not in open_resource["period"]

        closed = make_encounter(
            status=EncounterStatus.FINISHED,
            ended_at=make_encounter().started_at,
        )
        closed_resource = mappers.encounter_to_fhir(
            closed, identifier_system=IDENTIFIER_SYSTEM, patient_ref="Patient/p-1"
        )
        assert closed_resource["period"]["end"] == closed_resource["period"]["start"]

    def test_bed_travels_as_encounter_location(self):
        resource = mappers.encounter_to_fhir(
            make_encounter(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
            location_ref="Location/bed-9",
        )
        assert resource["location"] == [
            {"location": {"reference": "Location/bed-9"}}
        ]

    def test_priority_uses_act_priority(self):
        resource = mappers.encounter_to_fhir(
            make_encounter(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
        )
        coding = resource["priority"]["coding"][0]
        assert coding == {
            "system": mappers.V3_ACT_PRIORITY,
            "code": "UR",
            "display": "urgent",
        }


class TestObservationMapper:
    def test_numeric_value_becomes_quantity_with_ucum(self):
        resource = mappers.observation_to_fhir(
            make_observation(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
            encounter_ref="Encounter/e-1",
        )
        assert resource["status"] == "final"
        assert resource["code"]["coding"][0]["code"] == "8867-4"
        assert resource["valueQuantity"] == {
            "value": 82.0,
            "unit": "/min",
            "system": "http://unitsofmeasure.org",
            "code": "/min",
        }
        assert "valueString" not in resource

    def test_text_value_becomes_value_string(self):
        observation = make_observation(
            value_numeric=None, unit=None, value_text="Sin hallazgos"
        )
        resource = mappers.observation_to_fhir(
            observation,
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
            encounter_ref="Encounter/e-1",
        )
        assert resource["valueString"] == "Sin hallazgos"
        assert "valueQuantity" not in resource

    def test_vital_signs_category_only_for_loinc(self):
        loinc = mappers.observation_to_fhir(
            make_observation(),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
            encounter_ref="Encounter/e-1",
        )
        assert loinc["category"][0]["coding"][0]["code"] == "vital-signs"

        local = mappers.observation_to_fhir(
            make_observation(code_system="urn:saludred:codes", code="X-1"),
            identifier_system=IDENTIFIER_SYSTEM,
            patient_ref="Patient/p-1",
            encounter_ref="Encounter/e-1",
        )
        assert "category" not in local


class TestOrganizationMapper:
    def test_eps_is_payer_and_ips_is_provider(self):
        eps = make_organization(
            code="EPS-1",
            organization_type=OrganizationType.EPS,
            parent_organization_id=None,
        )
        ips = make_organization()
        eps_resource = mappers.organization_to_fhir(
            eps, identifier_system=IDENTIFIER_SYSTEM
        )
        ips_resource = mappers.organization_to_fhir(
            ips, identifier_system=IDENTIFIER_SYSTEM, part_of_ref="Organization/eps-1"
        )
        assert eps_resource["type"][0]["coding"][0]["code"] == "pay"
        assert ips_resource["type"][0]["coding"][0]["code"] == "prov"
        assert ips_resource["partOf"] == {"reference": "Organization/eps-1"}
        assert "partOf" not in eps_resource


class TestLocationMapper:
    def test_bed_carries_operational_status(self):
        for status, expected in [
            (BedStatus.AVAILABLE, "U"),
            (BedStatus.OCCUPIED, "O"),
            (BedStatus.RESERVED, "O"),
            (BedStatus.CLEANING, "H"),
            (BedStatus.BLOCKED, "C"),
            (BedStatus.MAINTENANCE, "C"),
        ]:
            resource = mappers.location_to_fhir(
                make_location(status=status),
                identifier_system=IDENTIFIER_SYSTEM,
                managing_organization_ref="Organization/ips-1",
            )
            assert resource["operationalStatus"]["code"] == expected
            assert resource["operationalStatus"]["system"] == mappers.V2_0116

    def test_physical_type_bed(self):
        resource = mappers.location_to_fhir(
            make_location(),
            identifier_system=IDENTIFIER_SYSTEM,
            managing_organization_ref="Organization/ips-1",
        )
        assert resource["physicalType"]["coding"][0]["code"] == "bd"
        assert resource["status"] == "active"

    def test_non_bed_has_no_operational_status(self):
        ward = make_location(
            location_type=LocationType.WARD, status=None, code="IPS-NORTE-S1"
        )
        resource = mappers.location_to_fhir(
            ward,
            identifier_system=IDENTIFIER_SYSTEM,
            managing_organization_ref="Organization/ips-1",
        )
        assert "operationalStatus" not in resource
        assert resource["physicalType"]["coding"][0]["code"] == "wa"
