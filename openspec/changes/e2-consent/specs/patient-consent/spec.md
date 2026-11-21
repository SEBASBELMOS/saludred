# patient-consent Specification

## Purpose

Patient × destination consent model for sharing clinical data (Ley 1581 / R32). Covers the `consents` table (additive migration), validity lifecycle, physician-facing registration/revocation endpoints, role enforcement, and audit trail.

## Requirements

### Requirement: Consent Model and Additive Migration

The system MUST persist consents in a new `consents` table via an additive Alembic migration whose `down_revision` is `b7e5d9a3c418`. Each row MUST bind exactly one destination: `organization_id` (in-network) XOR `interop_client_id` (partner, `interop_clients.client_id`), enforced by a DB CHECK constraint. `patient_id` MUST be NOT NULL.

- Migration MUST have upgrade/downgrade pairs, explicit constraint names via `op.f`, and non-unique partial indexes with `postgresql_where`/`sqlite_where`. Renewals require several rows per patient and destination; PostgreSQL registration serializes the overlap check with a transaction advisory lock.
- Migration MUST NOT alter, drop, or rewrite existing tables or columns (no destructive changes; head must remain after `b7e5d9a3c418`).

#### Scenario: Additive migration upgrades

- GIVEN the database is at head `b7e5d9a3c418`
- WHEN the new migration is applied
- THEN the `consents` table exists with the CHECK exactly-one-destination constraint
- AND no existing table or column was modified

#### Scenario: Additive migration downgrades

- GIVEN the `consents` table exists
- WHEN `alembic downgrade -1` runs
- THEN the table and its constraints are removed
- AND the schema returns to the `b7e5d9a3c418` state

### Requirement: Consent Registration

The system MUST allow registering a consent for a patient with exactly one destination (`organization_id` or `interop_client_id`, never both, never neither). `valid_from` MUST be required; `valid_until` MAY be null (indefinite until revoked). Registration MUST be rejected with `ConflictError` when the new validity window overlaps an existing valid (non-revoked, non-expired) consent for the same patient and destination (query-level enforcement; Postgres cannot express interval non-overlap).

#### Scenario: Register consent for a partner destination

- GIVEN an authenticated medico with staff scope over patient P and an existing interop client C
- WHEN the medico registers consent for P bound to `interop_client_id` C with `valid_from` in the past
- THEN the consent is persisted with status active
- AND a `CONSENT_CREATED` audit entry records actor, patient, and destination

#### Scenario: Exactly-one destination enforced

- GIVEN a registration payload
- WHEN both `organization_id` and `interop_client_id` are provided (or neither)
- THEN the API rejects it with 422 before persistence
- AND the DB CHECK constraint independently rejects direct writes

#### Scenario: Overlapping renewal rejected

- GIVEN an active consent for patient P and partner C with `valid_until` null
- WHEN a new consent for P and C is registered
- THEN the service raises `ConflictError` (HTTP 409)

### Requirement: Consent Validity and Renewal

The system MUST treat a consent as valid only when it matches the patient and destination, `valid_from <= now`, `valid_until` is null or `now < valid_until`, and it is not revoked. Partner destinations MUST be active. Windows are half-open `[valid_from, valid_until)`; an adjacent renewal is allowed. Renewal MUST be a new row (never an edit of the old one); expired consents remain readable for the audit trail.

#### Scenario: Consent expires at valid_until

- GIVEN a consent with `valid_until` in the past
- WHEN validity is evaluated
- THEN the consent is not valid

#### Scenario: Renewal preserves history

- GIVEN an expired consent row E for patient P and destination D
- WHEN a new consent for P and D is registered
- THEN row E remains unchanged and a new row is created

### Requirement: Consent Revocation

The system MUST support explicit revocation recording `revoked_at` and the acting user on every non-revoked row for the selected patient and destination, including future windows. Each revoked row has its own audit entry. Revoking an unknown consent MUST raise `NotFoundError`; revoking an already-revoked consent MUST raise `ConflictError` without another audit entry. Registration and revocation MUST enforce staff patient scope; listing MUST filter IPS-scoped actors to their patients.

#### Scenario: Revoke a valid consent

- GIVEN an active consent C for patient P
- WHEN an authorized user revokes C
- THEN `revoked_at` and actor are recorded
- AND a `CONSENT_REVOKED` audit entry is written
- AND C is no longer valid from that moment

### Requirement: Role Enforcement on Consent Endpoints

`POST/GET/DELETE /api/v1/consents` MUST be restricted to `ADMIN` and `IPS_CLINICAL_OPERATOR` via `authz.require_role`. Any other role MUST receive 403 and MUST NOT reach the service layer. Request schemas MUST use Spanish public field names with `extra="forbid"`, following `app/schemas/referral.py` conventions.

#### Scenario: Unauthorized role is rejected

- GIVEN an authenticated user with role `PATIENT` (or `SPECIALIST`)
- WHEN the user calls any consent endpoint
- THEN the API returns 403
- AND no consent row or audit entry is created

### Requirement: Consent Audit Trail

The system MUST write audit entries for consent events using new `AuditAction` values in `app/models/enums.py`: `CONSENT_CREATED` and `CONSENT_REVOKED` (matching the existing `*_CREATED`/`*_REVOKED` naming). Each entry MUST include the actor, the consent id, and the destination identity.

#### Scenario: Audit entries for lifecycle events

- GIVEN any successful register or revoke operation
- WHEN the operation commits
- THEN exactly one audit entry per created or revoked row with the corresponding `AuditAction` is persisted
- AND no audit entry is written when the operation fails validation
