# minimal-fhir-bundle Specification

## Purpose

Partner-bound minimized FHIR Bundle builder in `app/fhir/bundle.py`. Produces the R32 data-minimized record sent to the partner, alongside the existing full `clinical_bundle` (in-network, unchanged).

## Requirements

### Requirement: Partner Bundle Builder

The system MUST provide a new builder (e.g. `minimal_partner_bundle`) in `app/fhir/bundle.py` that produces a `collection` Bundle for a patient intended ONLY for partner destinations. It MUST be resource-agnostic: it MUST accept the report collection without hardcoding its resource type, so it works with `RiskAssessment` today and with `DiagnosticReport` after E1 without builder changes.

#### Scenario: Builder emits a collection Bundle

- GIVEN a patient P, their profile-relevant Observations, and approved reports
- WHEN the partner bundle builder runs
- THEN it returns a `Bundle` with `type: collection`
- AND `entry` contains exactly: Patient, the Observations, and one entry per approved report

### Requirement: Patient Minimization

The Patient resource in a partner bundle MUST keep ONLY the federated subject identifier (`patients.id_federado`, system `settings.federated_system`) among identifiers, and MUST NOT include document identifiers (`:documento`, `:numero`, `:patient-id` systems), `telecom` (phone/email), or `address`. The existing in-network `clinical_bundle` MUST remain byte-equivalent in behavior to today.

#### Scenario: Document identifiers dropped

- GIVEN patient P has a document number and a federated id
- WHEN the partner bundle is built
- THEN `Patient.identifier` contains only the federated identifier
- AND no identifier with a documento/numero system is present

#### Scenario: Contact data dropped

- GIVEN patient P has phone, email, and address populated
- WHEN the partner bundle is built
- THEN the Patient resource has no `telecom` and no `address`

#### Scenario: In-network bundle unchanged

- GIVEN the same patient P
- WHEN `clinical_bundle` runs (in-network export)
- THEN the full record is emitted exactly as before this change (document identifiers, telecom, address, all resources)

### Requirement: Content Minimization

The partner bundle MUST include profile-relevant Observations for the patient and approved AI reports, and MUST NOT include Encounters or any other resource class the full bundle exports. The exact profile-relevance filter MAY be defined in the design phase, but the inclusion set MUST be testable.

#### Scenario: Only profile-relevant Observations included

- GIVEN patient P with profile-relevant and non-profile Observations
- WHEN the partner bundle is built
- THEN only the profile-relevant Observations appear
- AND no Encounter entries appear

#### Scenario: Approved reports preserved across resource types

- GIVEN approved reports rendered as `RiskAssessment` today
- WHEN the partner bundle is built
- THEN each approved report appears exactly once
- AND the same call works unchanged when E1 switches reports to `DiagnosticReport`
