# Proposal: e2-consent — Consent by Destination + Minimal Partner Bundle

## Intent

R32 is the only unimplemented official requirement: no consent record exists, so nothing blocks an outbound referral to the partner. Add a patient × destination × validity consent model, physician registration/revocation, a reusable validity gate the future E1 outbound sender must call, and a minimized partner-bound Bundle.

## Decisions (open questions resolved)

1. **Destination identity**: hybrid `consents` table — nullable FKs `organization_id` / `interop_client_id` + CHECK exactly-one-set. Partner destination = `interop_clients.client_id`. *Why*: partner has no `organizations` row; one model covers in-network and partner (exploration Approach 3).
2. **Validity**: `valid_from` required; `valid_until` nullable (null = indefinite until revoked). Renewal = new row. Revocation = explicit flow recording `revoked_at` + actor. *Why*: avoids fake expiry dates; new-row renewal and explicit revocation preserve audit trail (Ley 1581).
3. **Roles**: register/revoke = ADMIN + IPS_CLINICAL_OPERATOR via `authz.require_role`. *Why*: existing authz; agent's `proponer_remision` targets LOCAL specialists → out of scope.
4. **Gate placement**: reusable `consents.require_valid(db, patient_id, destination)` — unit-tested standalone now, documenting the exact call contract E1 wires into outbound `InteropSender`; plus pre-check in `referrals.create` when destination resolves to a partner. *Why*: outbound send path does not exist until E1; gate must be provable without it.
5. **Minimization**: partner-bound bundles only; resource-agnostic builder (survives E1's RiskAssessment→DiagnosticReport switch). Patient drop-list: document identifiers, telecom, address. Keep profile-relevant Observations + approved reports. *Why*: R32 targets the partner flow; in-network full record stays unchanged.
6. **UI**: endpoint-only (route + schema + tests); UI deferred as optional stretch. *Why*: R32 allows "endpoint o UI"; UI has no partner destinations to pick until E1.

## Scope

### In Scope

- Consent model + additive Alembic migration after `b7e5d9a3c418` (upgrade/downgrade pairs, explicit `op.f` constraint names)
- `POST/GET/DELETE /api/v1/consents` register/list/revoke (medico/admin, Spanish public schema, `extra="forbid"`)
- Validity gate service + referral-creation pre-check for partner destinations
- Minimal partner bundle builder in `app/fhir/bundle.py`
- `AuditAction` values (CONSENT_CREATED/REVOKED) + consent status enum + tests

### Out of Scope

- Wiring gate into E1 outbound `InteropSender` (contract documented only)
- Registration UI (stretch task)
- Minimizing in-network bundles
- docs/ updates (E5; R32 evidence row at E2 close)

## Capabilities

### New Capabilities

- `patient-consent`: consent model, migration, validity/lifecycle rules, register/revoke endpoints
- `interop-consent-gate`: `require_valid` contract + partner referral pre-check
- `minimal-fhir-bundle`: partner-bound minimized Bundle builder

### Modified Capabilities

None — `openspec/specs/` is empty; all capabilities are new.

## Approach

Exploration Approach 3. Single hybrid `consents` table; query-level validity check (Postgres cannot express non-overlapping-interval uniqueness — accept query enforcement + tests). Additive migration matching existing style (`postgresql_where`/`sqlite_where` partials). Gate as service function raising `ForbiddenError`/`ConflictError`. Tests follow `tests/test_referrals.py` / interop patterns.

## Affected Areas

| Area | Impact |
|------|--------|
| `app/models/consent.py`, `app/models/__init__.py` | New |
| `migrations/versions/` | New (after `b7e5d9a3c418`) |
| `app/models/enums.py` | Modified (CONSENT_* audit actions, ConsentStatus) |
| `app/services/consents.py` | New |
| `app/api/routes/consents.py`, `app/schemas/consent.py` | New |
| `app/fhir/bundle.py` | Modified (minimal partner builder) |
| `app/services/referrals.py` | Modified (partner pre-check) |
| `tests/test_consents*.py` | New |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Outbound send path absent until E1 | High | Standalone gate tests + documented E1 call contract |
| Overlapping-validity uniqueness not expressible in Postgres | Med | Query-level enforcement + tests |
| Minimizer diverges from E1 DiagnosticReport switch | Low | Resource-agnostic builder |
| Wrong destination identity choice | Low | Hybrid FKs cover both; additive, rework-free |

## Rollback Plan

Additive migration: `alembic downgrade -1` on ASUS test path (never Neon without authorization). Gate is fail-closed inside new code paths only; removing the call site and dropping the table restores current behavior. New route/schema change no existing endpoint shape.

## Dependencies

- E1 wires `require_valid` into outbound `InteropSender`
- No partner assignment needed; destination resolves from `interop_clients`

## Success Criteria

- [ ] Migration upgrades and downgrades cleanly on ASUS test path
- [ ] Gate tests: missing/expired/revoked/wrong-destination → blocked; valid → pass
- [ ] Endpoint tests: medico/admin register/revoke; other roles 403
- [ ] Partner bundle drops document identifiers, telecom, address; keeps federated subject identifier + approved reports + relevant Observations; in-network bundle unchanged
- [ ] Partner referral creation without valid consent rejected
