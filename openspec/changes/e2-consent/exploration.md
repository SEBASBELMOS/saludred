# Exploration: e2-consent (E2 · R32 · Consent by destination + minimal Bundle)

## Current State

**R32 official wording** (`docs/ENTREGA_FINAL.md`, "Requisitos oficiales (16/11)", row R32):

> | R32: Consent por destino y Bundle mínimo | Pendiente | Sin modelo ni bloqueo por consentimiento registrado | Siguiente lote E2: modelo, migración aditiva, registro médico, validación de vigencia/destino y minimización del Bundle |

Handoff E2 scope (`PROYECTO/HANDOFF-opencode-20261117.md` lines 144–149): Consent model (patient × destination institution × validity), additive migration, endpoint or UI for a physician to register it, **block referral to the partner without valid consent for that destination**, minimize Bundle content. Order: E2 (Consent) → E1 (Bundle round trip) → E5 (docs).

**What exists today:**

- **No consent anywhere.** Zero matches for consent/consentimiento/Consent in backend or frontend code (only in docs/NORMATIVA.md "Limitaciones conocidas" and the RAG corpus). R32 is the only unimplemented row of the new official requirements.
- **Alembic head is `b7e5d9a3c418`** (`migrations/versions/b7e5d9a3c418_interop_clients_nonces_and_federated_id.py`; no migration lists it as down_revision). Migration style: additive-first, docstring explains reversibility, explicit constraint names via `op.f`, upgrade/downgrade pairs, partial unique indexes with `postgresql_where` + `sqlite_where`.
- **Referral domain (R08):** `app/models/referral.py` (`Referral`: patient_id, referring_user_id NULLABLE for partner-origin, specialist_user_id, motivo, estado, origen_externo/referencia_externa, idempotency_key). Creation: `POST /api/v1/referrals` (medico/`IPS_CLINICAL_OPERATOR` only) → `app/services/referrals.py::create`. Inbound partner ServiceRequest: `app/services/interop/receptor.py::process` → `choose_specialist` (performer identifier → `organizations.code` via `perfil.performer_organization_code`, fallback `INTEROP_DEFAULT_SPECIALIST`) → `referrals.create_external`.
- **No production outbound-to-partner flow yet.** `InteropSender` (`app/services/interop/cliente.py`) builds/sends `Observation`/`ServiceRequest` with federated subject, HMAC, token retry — but is invoked ONLY from `scripts/probar_sistema.py` and tests. The real Bundle-send flow is E1. **Therefore the E2 consent gate must be a reusable service function that E1 calls**, plus direct tests now.
- **Partner identity:** `interop_clients` (client_id, name, scopes, encrypted signing secret; registered via `app/services/interop/clientes.py`). **There is NO Organization row for a partner** — `organizations` only holds in-network EPS/IPS. Destination institution of an outbound referral is therefore `interop_clients.client_id` (and `organizations.code` for in-network IPS in `performer[]`).
- **Federated patient id:** `patients.id_federado` (HMAC-SHA256, filled on insert/update when FEDERATED_SECRET set), `app/services/interop/federado.py`, `subject.identifier.system = settings.federated_system`. Partner messages carry the federated id, never the document.
- **Bundle export today:** `GET /api/v1/patients/{id}/fhir-bundle` → `app/services/fhir_exchange.py::export_bundle` → `app/fhir/bundle.py::clinical_bundle`: Patient (`mappers.patient_to_fhir` — three identifiers INCLUDING document number `CC-…`, name, gender, birthDate, telecom phone+email, address), up to 200 Encounters, all scoped Observations, approved AI reports as `RiskAssessment`. Import/validation in the same module. This is the *un-minimized* full record; nothing destination-aware exists.
- **Auth/roles:** user JWT (aud `saludred-api`) + system token (aud `saludred-interop`, 10 min, scopes) — `app/core/authz.py` (`require_role`, `ensure_org_scope`, `org_filter_for`), `app/api/deps.py`. Medico = `IPS_CLINICAL_OPERATOR`; no consent audit actions exist in `AuditAction` (`app/models/enums.py`).
- **Tests:** pytest with in-memory ORM factories (`tests/factories.py`), TestClient + reactive helpers (`tests/test_referrals.py`, `tests/test_interop_receiver.py`, `tests/test_interop_sender.py`, `tests/test_interop_sql.py`), faked FHIR (`tests/fhir_helpers.py::FakeFhir`), faked ML client (`tests/conftest.py`). Migration tests pattern: `tests/test_interop_sql.py`, `tests/test_rag_migrations.py`.

## Affected Areas

- `app/models/` (new `consent.py`) — new `Consent` model; register in `app/models/__init__.py`.
- `migrations/versions/` — new additive migration after `b7e5d9a3c418`.
- `app/models/enums.py` — new `AuditAction` values (e.g. CONSENT_CREATED / CONSENT_REVOKED), maybe a consent status enum.
- `app/services/` (new `consents.py` or `interop/consents.py`) — register/list/revoke + the validity gate `require_valid_consent(db, patient_id, destination)`.
- `app/api/routes/` (new or existing `referrals.py`/`interop.py`) — physician-facing REST endpoints (`/api/v1/consents` or under patients) with `authz.require_role(..., IPS_CLINICAL_OPERATOR, ADMIN)`.
- `app/fhir/bundle.py` — new minimal/partner bundle builder (federated subject identifier only, no telecom/address/document, limited resources).
- `app/services/interop/cliente.py` (and the future E1 sender) — call site of the consent gate before sending ServiceRequest/Bundle.
- `app/schemas/` — new consent schemas (Spanish public contract names, `extra="forbid"`, like `app/schemas/referral.py`).
- `frontend/src/api/` + `frontend/src/features/` — registration UI if in scope (pattern: `referrals.ts` / `features/referrals/`).
- `docs/` (E2 evidence, Spanish, read-only today): `ENTREGA_FINAL.md` R32 row, `EVENTOS.md` (new event), `INTEROP.md`, `STRIDE.md` residual line, `NORMATIVA.md`.
- `tests/` — new `test_consents*.py` following referral/interop test patterns.

## Approaches

1. **Consent keyed to `interop_clients` (partner system as destination).**
   - Pros: matches the handoff's "remisión al socio"; the partner has no `organizations` row; E1's outbound flow targets a partner client; simplest truth for the block.
   - Cons: partner not assigned yet; outbound flow (E1) does not exist to integrate with; UI has nothing to pick from today.
   - Effort: Low–Medium.

2. **Consent keyed to `organizations.id` (in-network IPS as destination).**
   - Pros: reuses the existing institution picker/beds destination concept; usable immediately by local flows.
   - Cons: does NOT cover the partner — R32 explicitly asks to block the *partner* referral; would need an org row per partner (schema-smell).
   - Effort: Low.

3. **Hybrid destination (recommended):** single `consents` table with `patient_id` NOT NULL, `organization_id` NULLABLE FK, `interop_client_id` NULLABLE FK, CHECK (exactly one destination set); validity `valid_from` required + `valid_until` optional (indefinite); query-based validation "a valid consent exists for this (patient, destination) now"; additive migration; audit + domain event on create/revoke. The gate lives in a service both E2 tests and E1's sender call.
   - Pros: covers in-network and partner destinations with one model; additive; no schema rewrite later when the partner appears.
   - Cons: slightly more validation code; partial unique index on overlapping validity is not expressible in Postgres (accept query-level enforcement + tests).
   - Effort: Medium.

## Recommendation

**Approach 3 (hybrid destination)** with the gate implemented as a reusable service (`consents.require_valid(...)` raising `ForbiddenError`/`ConflictError`), registered endpoints for the physician, and a new `minimal_partner_bundle(...)` builder in `app/fhir/bundle.py` that emits the federated identifier in `subject.identifier` and drops document identifiers, telecom, address and non-essential resources. E2 proves the gate with direct unit tests; E1 wires it into the sender.

## Risks

- The outbound send path does not exist yet — E2's block cannot be end-to-end tested against a partner; the gate must be tested standalone and documented for E1.
- Partner destination identity is ambiguous (client_id vs org code); choosing wrong means a migration rework in E1.
- Minimization could diverge from E1's DiagnosticReport switch (RiskAssessment → DiagnosticReport) — keep the minimizer resource-agnostic.
- Validity overlap semantics (renewals, revocation) are not covered by any existing pattern; needs explicit proposal decisions.
- `docs/` updates are part of E5 but the R32 evidence row in `ENTREGA_FINAL.md` is expected at E2 close.

## Ready for Proposal

Yes. The proposal must resolve the 7 open questions below and reference the file inventory in the exploration return.
