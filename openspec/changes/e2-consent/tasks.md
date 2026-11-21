# Tasks: e2-consent — Consent by Destination + Minimal Partner Bundle

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,100 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | WU1 → WU2 → WU3 → WU4 → docs |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Model + migration + enums + factories | PR 1 | Phase 1; tests included |
| 2 | Gate + consent service | PR 2 | Depends WU1 |
| 3 | API + referral pre-check | PR 3 | Depends WU2 |
| 4 | Minimal partner bundle | PR 4 | Independent of WU2/WU3 |
| 5 | Docs (R32 row + EVENTOS note) | closing agent | After verify only |

No git writes during SDD — units are planning slices only.

## Phase 1: Foundation

- [x] 1.1 RED: `tests/test_consents_migration.py` — load revision by `down_revision=="b7e5d9a3c418"`; assert AUDIT_ACTIONS == enum ∪ {CONSENT_CREATED, CONSENT_REVOKED}, CHECK, downgrade drops. Update `tests/test_rag_migrations.py::test_head_migration_lists_every_action_of_the_enum` → new revision. Run on ASUS `saludred-api:latest` (RED).
- [x] 1.2 GREEN: `app/models/enums.py` (+AuditAction.CONSENT_CREATED/REVOKED, ConsentStatus); `migrations/versions/<rev>_consents_table_and_audit_actions.py` (`down_revision="b7e5d9a3c418"`, op.f names, partial indexes postgresql_where/sqlite_where, audit CHECK swap, downgrade restores). GREEN on ASUS; then ASUS `alembic upgrade head --sql` review + up/down cycle.
- [x] 1.3 `app/models/consent.py` (exactly_one_destination CHECK, indexes, no soft-delete) + export in `app/models/__init__.py`; `tests/factories.py` add `make_consent`, `make_interop_client`. Verify via 1.1 tests.

## Phase 2: Service + Gate

- [x] 2.1 RED: `tests/test_consents_service.py` — gate missing/expired/revoked/not-yet-valid/wrong-dest→ForbiddenError, valid→pass; overlap→ConflictError; renewal new row; revoke NotFound/Conflict; status derivation. Run on ASUS (RED).
- [x] 2.2 GREEN: `app/services/consents.py` — `require_valid_consent`, `register_consent`, `revoke_consent`, `list_consents` per design contracts. GREEN on ASUS.

## Phase 3: API + Partner Pre-Check

- [x] 3.1 RED: `tests/test_consents_api.py` — 201/409/422/403, Spanish schemas, audit rows, no-audit-on-failure; referral pre-check scenarios (partner blocked/allowed, in-network unchanged). Run on ASUS (RED).
- [x] 3.2 GREEN: `app/schemas/consent.py` (`extra="forbid"`), `app/api/routes/consents.py` (require_role ADMIN+IPS_CLINICAL_OPERATOR), `app/main.py` include router. GREEN on ASUS.
- [x] 3.3 GREEN: `app/services/referrals.py::create` — optional `interop_destination` kwarg; gate before any write; None = unchanged. GREEN on ASUS.

## Phase 4: Partner Bundle

- [x] 4.1 Tests written first: `tests/test_minimal_bundle.py` — collection type; federated identifier only; no telecom/address/document ids; profile-relevant Observations only, no Encounters; reports once, resource-agnostic; `clinical_bundle` unchanged. Execution pending on ASUS; RED not run here.
- [x] 4.2 Implementation written: `app/fhir/bundle.py` — `minimal_partner_bundle` (`Sequence[dict]` reports) + minimal Patient builder + profile filter. Verification pending on ASUS; GREEN not claimed.

## Phase 5: Verification

- [ ] 5.1 Full suite on ASUS: `python -m pytest tests -q` — baseline 1192+5 git-skips grows additively.
- [ ] 5.2 ASUS: alembic upgrade head → downgrade -1 (never Neon).

## Phase 6: Docs (FINAL — closing agent, after verify)

- [ ] 6.1 Update `docs/ENTREGA_FINAL.md` R32 row (→ Implementado, evidence) + `docs/EVENTOS.md` consent note (audit-only, no domain events). Final state only after verification results; not mid-batch.
