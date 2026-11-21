# interop-consent-gate Specification

## Purpose

Reusable validity gate that blocks outbound data sharing to a destination without a valid consent. Implemented in `app/services/consents.py`, invoked by `app/services/interop/envio.py::send_to_partner` before handing a minimized Bundle to `InteropSender`. Local referral creation retains its existing idempotent behavior.

## Requirements

### Requirement: Gate Contract for E1

The system MUST expose `require_valid_consent(db, *, patient_id, destination)` in `app/services/consents.py`. `destination` MUST resolve to exactly one destination kind: an in-network `organizations` identity or a partner `interop_clients.client_id`. The gate MUST return normally when a valid consent exists for the (patient, destination) pair and MUST raise `ForbiddenError` otherwise. It MUST be a pure service function (no HTTP dependencies) so E1 can call it from `InteropSender` without changes.

#### Scenario: Documented call contract

- GIVEN E1's outbound sender intends to send a `ServiceRequest` or Bundle to partner C for patient P
- WHEN E1 calls `require_valid_consent(db, patient_id=P, destination=(interop_client, C))`
- THEN no exception means the send MAY proceed
- AND `ForbiddenError` means the send MUST abort before any HTTP call to the partner

### Requirement: Validity Evaluation

The gate MUST evaluate validity as: consent matches patient AND destination, `valid_from <= now`, (`valid_until IS NULL` OR `now < valid_until`), and not revoked. Partner destinations MUST have `is_active = true`. Missing, expired, revoked, not-yet-valid, inactive-partner and wrong-destination consents MUST all raise `ForbiddenError` (mapped to HTTP 403 by `app/api/error_handlers.py`).

#### Scenario: Missing consent blocks

- GIVEN patient P has no consent row for partner C
- WHEN the gate is evaluated for (P, C)
- THEN `ForbiddenError` is raised

#### Scenario: Expired consent blocks

- GIVEN the only consent for (P, C) has `valid_until` in the past
- WHEN the gate is evaluated for (P, C)
- THEN `ForbiddenError` is raised

#### Scenario: Revoked consent blocks

- GIVEN the only consent for (P, C) has `revoked_at` set
- WHEN the gate is evaluated for (P, C)
- THEN `ForbiddenError` is raised

#### Scenario: Wrong destination blocks

- GIVEN patient P has a valid consent for organization O but none for partner C
- WHEN the gate is evaluated for (P, C)
- THEN `ForbiddenError` is raised

#### Scenario: Valid consent passes

- GIVEN an active, unexpired, unrevoked consent for (P, C)
- WHEN the gate is evaluated for (P, C)
- THEN the call returns normally

### Requirement: Partner Send Pre-Check

`app/services/interop/envio.py::send_to_partner` MUST load the patient and check staff scope before calling `require_valid_consent`. On consent denial it MUST persist `INTEROP_REJECTED` and raise 403 without invoking the sender. On success it MUST build `minimal_partner_bundle` using approved reports and pass it to the injectable sender in `cliente.py`. Partner response/Task processing is outside this change. In-network referral creation MUST remain unchanged (no gate call).

#### Scenario: Partner referral blocked without consent

- GIVEN a medico sends patient P's clinical data to partner client C
- AND P has no valid consent for C
- WHEN `send_to_partner` runs
- THEN `ForbiddenError` propagates (HTTP 403)
- AND the sender is not called; an `INTEROP_REJECTED` audit entry is written

#### Scenario: Partner referral allowed with consent

- GIVEN P has a valid consent for partner C
- WHEN `send_to_partner` runs for (P, C)
- THEN the minimal Bundle is handed to the sender

#### Scenario: In-network referral unaffected

- GIVEN a referral whose destination resolves to an in-network specialist/organization
- WHEN `referrals.create` runs
- THEN the gate is not invoked and creation behaves exactly as today
