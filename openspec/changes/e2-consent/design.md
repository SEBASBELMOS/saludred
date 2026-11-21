# Design: e2-consent — Consent by Destination + Minimal Partner Bundle

## Technical Approach

Three additive capabilities: a hybrid-destination `consents` table (exactly-one CHECK), a reusable validity-gate service, and a partner-only minimized Bundle builder. Follows existing patterns: referral-style explicit columns, `enum_column` VARCHAR+CHECK, partial indexes with dual `where`, additive migration after `b7e5d9a3c418`, domain errors, `authz.require_role`.

## Architecture Decisions

| Option | Tradeoff | Decision |
|---|---|---|
| One table, nullable FK pair + CHECK | non-overlap unenforceable in DB (accept query enforcement) | **hybrid `consents`** |
| Two tables (org/client consents) | splits gate queries, duplicate lifecycle | rejected |
| JSONB destination | no FK integrity | rejected |

| Option | Tradeoff | Decision |
|---|---|---|
| Stored `estado` column | duplicates validity facts (two columns eventually disagree — project single-fact rule) | rejected |
| Derived status + public `ConsentStatus` enum (`activa`/`revocada`/`expirada`) for DTOs | computed from `valid_from`/`valid_until`/`revoked_at` | **chosen** |

**Partner pre-check**: `referrals.create` cannot resolve a partner today (specialist-only). Add optional kwarg `interop_destination: str | None` (client_id); when set, gate runs before any write — no referral, no audit on `ForbiddenError`. `None` = unchanged in-network path. E1 calls `require_valid_consent` directly from `InteropSender` (documented contract).

**Minimizer agnosticism**: `minimal_partner_bundle` accepts pre-built report resources (`Sequence[dict]`), not `RiskAssessment` objects — caller builds them (`risk_assessment` today, DiagnosticReport mapper after E1).

**Federated id NULL** (unbackfilled patient): omit the identifier entry; partner 202 reconciliation resolves it (existing interop flow).

## Data Flow

```
POST /api/v1/consents ── require_role(ADMIN|MEDICO) ── register_consent
   patient_access scope → resolve destination → overlap query (ConflictError 409)
   → insert → trail.audit(CONSENT_CREATED) → commit

E1 (future): InteropSender ── require_valid_consent(db, patient_id, destination)
   ── no valid row → ForbiddenError (403) → abort BEFORE any HTTP to partner

referrals.create ── interop_destination set? ── gate (ForbiddenError → nothing written)
                 ── None → today's behavior, byte-for-byte unchanged

partner bundle: Patient(minimized) + profile-relevant Observations + approved reports
   → Bundle(type=collection)        [clinical_bundle untouched]
```

## File Changes

| File | Action | Description |
|---|---|---|
| `app/models/consent.py` | Create | `Consent` model (below) |
| `app/models/__init__.py` | Modify | export `Consent` (create_all/Alembic visibility) |
| `app/models/enums.py` | Modify | `AuditAction.CONSENT_CREATED/CONSENT_REVOKED`; `ConsentStatus` |
| `migrations/versions/<rev>_consents_table_and_audit_actions.py` | Create | additive; `down_revision="b7e5d9a3c418"` |
| `app/services/consents.py` | Create | gate + register/list/revoke |
| `app/schemas/consent.py` | Create | `ConsentCreate`/`ConsentRead` (Spanish, `extra="forbid"`) |
| `app/api/routes/consents.py` | Create | router `/api/v1/consents` |
| `app/main.py` | Modify | include router |
| `app/services/referrals.py` | Modify | `interop_destination` gate pre-check |
| `app/fhir/bundle.py` | Modify | `minimal_partner_bundle` + minimal Patient builder |
| `tests/test_consents_service.py`, `tests/test_consents_api.py`, `tests/test_consents_migration.py`, `tests/test_minimal_bundle.py` | Create | TDD-first |
| `tests/factories.py` | Modify | `make_consent`, `make_interop_client` |
| `tests/test_rag_migrations.py` | Modify | head enum-parity → new revision |

## Interfaces / Contracts

```python
# app/services/consents.py
Destination = tuple[Literal["organization"], uuid.UUID] \
            | tuple[Literal["interop_client"], str]        # business client_id

def require_valid_consent(db, *, patient_id, destination) -> None
    # raises ForbiddenError; pure service, no HTTP deps (exact E1 contract)
def register_consent(db, *, actor, patient_id, organization_id=None,
                     interop_client_id=None, valid_from, valid_until=None) -> Consent
    # InvalidValueError both/neither; NotFoundError unknown destination;
    # ConflictError overlapping live consent (query-level, no DB constraint)
def revoke_consent(db, *, actor, consent_id) -> Consent    # NotFound / Conflict
def list_consents(db, user, *, paciente_id=None) -> list[Consent]
```

```python
# app/models/consent.py — non-obvious parts
__table_args__ = (
    CheckConstraint(
        "(organization_id IS NULL AND interop_client_id IS NOT NULL)"
        " OR (organization_id IS NOT NULL AND interop_client_id IS NULL)",
        name="exactly_one_destination",      # → ck_consents_exactly_one_destination
    ),
    Index("ix_consents_patient_id_organization_id", "patient_id", "organization_id"),
    Index("ix_consents_patient_id_interop_client_id", "patient_id", "interop_client_id"),
)
# patient_id FK patients RESTRICT NOT NULL; organization_id FK organizations NULL;
# interop_client_id FK interop_clients.id NULL  (UUID PK, string resolved at service)
# valid_from NOT NULL; valid_until NULL; created_at/created_by; revoked_at/revoked_by
# no soft delete — rows immutable like referrals; renewal = new row
```

Audit-action swap: migration drops/recreates the `audit_action` CHECK on `audit_log` with `PREVIOUS_AUDIT_ACTIONS + (CONSENT_CREATED, CONSENT_REVOKED)` (a2f7c4d19b60 pattern); downgrade restores the previous list. Errors map via existing `error_handlers` (403/404/409/422).

## Testing Strategy (STRICT TDD)

- **RED first**: failing tests per spec scenario, then implement. Run only on the ASUS disposable container (`python -m pytest tests -q`, `--network none`, read-only mounts); Mac edit → ASUS run loop. Nothing executes on Mac.
- **Schema visibility**: `tests/conftest.py` runs neither migrations nor `create_all` — it points `DATABASE_URL` at a void host. Each DB test builds its own `sqlite://` StaticPool engine + `Base.metadata.create_all` (the `db`/`client_as` fixtures in `tests/test_bed_rules.py`, reused by `tests/test_referrals.py`). The `consents` table appears via `create_all` because the model is registered in `app/models/__init__.py`. Alembic upgrade/downgrade is verified manually on the ASUS disposable container (`alembic upgrade head --sql` review first), not in pytest.
- **SQLite notes**: CHECK uses plain `IS NULL`/`IS NOT NULL` booleans; `PRAGMA foreign_keys=ON` (fixture); every datetime stored/bound tz-aware UTC so SQLite string comparison stays consistent.
- **git-skips**: new tests never invoke git — the 5 git-dependent secret-scanner skips stay the only skips (baseline 1192 passed + 5 skipped grows additively).

| Layer | What | How |
|---|---|---|
| Unit | gate: missing/expired/revoked/not-yet-valid/wrong-dest/valid; overlap; minimization | pure fn + in-memory SQLite |
| Integration | 201/409/422/403 endpoints, audit rows, partner referral pre-check | TestClient + `db` fixture |
| Static | migration chain, CHECK present, audit enum parity | importlib load (test_rag_migrations pattern) |

## Migration / Rollout

Additive only; downgrade drops table + restores audit CHECK. Rollback: remove gate call site, drop table. No Neon operations; verification only on ASUS.

## Open Questions

- None blocking. Migration revision id generated at apply; tests glob by `down_revision`.
