> **Prompt P04 · Wave W1 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-003, ADR-004, ADR-011; shared/04 §E; shared/10 §D. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P04 — Identity, Tenancy, Row- and Field-Level Access Control

## Mission
Implement granular, deny-by-default access control across **every channel** (UI/API, exports, reports, search, AI agent, MCP, webhooks, notifications), with row-level
and field-level policies, multi-company scoping, SoD, delegated identities for AI/MCP, and PostgreSQL RLS as a second line of defense (ADR-003/004). Requirement UR-21.

## Owned paths
`backend/iam/**`, `deploy/keycloak/realm-template.json` (via CCR to P03 for compose wiring), `frontend/src/features/iam/**`.

## Dependencies
G0 (kernel tenancy, contracts). Consumers: everyone. Publish `iam/testing/fakes.py` (an allow-all-with-recording policy for unit tests of other modules) on day 1. Real policy is mandatory in
cross-module and security tests.

## Domain model
* `Tenant`(plan, region, status, data_residency), `Company`(tenant, NIT + DV, razón social, NIIF group, currency COP, tax regime flags, address, municipality DANE code, logo),
  `Branch`, `CostCenter` (hierarchical), `User` (mirrors the IdP subject; MFA status), `Membership`(user, tenant, companies[]), `Role` (templates from contracts + custom), `RoleAssignment`
  (user, role, scope: company/branch/warehouse/cost center, valid_from/to), `Policy` (ABAC rules), `FieldPolicy`, `SoDRule`, `ServiceAccount`, `DelegatedGrant` (AI/MCP client acting
  on behalf of a user: scopes, expiry, allowed tools, max amount per action), `BreakGlassSession`, `ConsentRecord` (Ley 1581: purpose, text version, channel, timestamp, revoked_at).
* **Row policy language** (stored as JSON, compiled to Django Q + SQL): predicates over resource attributes and principal attributes, for example
  `{"resource":"sales.invoice","effect":"allow","actions":["read"],"where":{"all":[{"attr":"company_id","op":"in","value":"$principal.company_ids"},{"attr":"salesperson_id","op":"eq","value":"$principal.employee_id"}]}}`.
  Deny overrides allow. No match → deny.
* **Field policy:** per resource property, readable/writable/masked by role and classification; masking strategies (`full`, `last4`, `hash`, `range` for salaries, for example "3M–4M").
  Unreadable fields are **omitted** from responses (not null), filters on unreadable fields are rejected (`400 filter_not_allowed` to prevent inference), and sorting and aggregation over them is rejected.

## Enforcement
1. `iam.policy.authorize()`, `scope_queryset()`, `field_mask()`, `check_sod()` as the single path (ADR-004). `SecuredViewSet` and `SecuredSerializer` base classes apply them automatically.
   `iam.export.secure_rows()` is used by dataio, reporting, AI, and MCP.
2. **RLS SQL** generated per tenant table by a migration helper `iam.rls.enable(table, company_scoped=True)`: ENABLE + FORCE RLS, policies on `tenant_id = current_setting('app.tenant_id')::uuid`
   and `company_id = ANY(current_setting('app.company_ids')::uuid[])`. A CI test enumerates all tables with `tenant_id` and fails if RLS is missing.
3. **Policy cache** keyed by `(tenant, principal, policy_version)`; a version bump on any change; revocation effective ≤ 5 s (test).
4. **OIDC with Keycloak:** a realm per environment, organizations/tenants mapped by claim; MFA (TOTP/WebAuthn) mandatory for admin/finance/payroll roles; password policy per ASVS
   5.0 (length ≥ 12 and breached-password check if available); session idle timeout 30 min (configurable); refresh-token rotation; access tokens 10 min.
5. **Delegated tokens for AI/MCP:** OAuth token exchange (RFC 8693) or a scoped internal token. Principal = the user, actor = the agent/client. `principal_type` ai_agent/mcp_client is recorded on every audit
   record. Effective permission = user permission ∩ grant scopes ∩ tool allow-list. Amount limits and "requires human confirmation" flags are enforced server-side.
6. **SoD:** rule examples: create vendor ≠ approve vendor payment; post payroll ≠ approve payroll payment; edit bank account ≠ approve payment run. Violations block with `sod_violation`;
   a documented exception workflow needs dual approval.
7. **Break-glass:** time-boxed (≤ 4 h), reason, approver, an alert to tenant admins, everything audited with a flag.
8. **Rate limiting** per principal and per tenant (API Top 10 API4), and login throttling.
9. **Admin UI:** roles (matrix view), policies (visual builder with "test as user" simulator showing which rows/fields are visible), assignments with validity, access review
   campaign export `[A]`, consent records viewer.

## API (module fragment `iam.yaml`)
`/api/v1/iam/companies`, `/branches`, `/cost-centers`, `/users`, `/roles`, `/role-assignments`, `/policies`, `/field-policies`, `/sod-rules`, `/service-accounts`,
`/delegated-grants`, `/break-glass:open|:close`, `/consents`, `/me` (effective permissions and field masks, used by the UI to hide controls), `/policies:simulate` (as user X on resource Y).

## Required tests (T-P04-nn) — security critical, ≥ 98% branch coverage
01 RLS present on all tenant tables. 02 Cross-tenant read/write blocked at the DB even if the app filter is removed (test monkeypatches scope_queryset to a no-op). 03 Unbound session sees 0 rows.
04 Company scoping: a user limited to company A cannot see B in list/detail/search/export/report/AI/MCP. 05 Row policy "own customers" for a salesperson. 06 Warehouse-scoped user.
07–12 Field masking on salary, bank account, cost, margin, cédula, health data through API, export (CSV/XLSX), report builder, AI answers (including aggregates), MCP tool results,
webhooks. 13 Filter/sort on a masked field rejected. 14 BOLA: an auto-generated test over all `{id}` endpoints with a foreign-tenant id → 404 (not 403, to avoid enumeration). 15 BFLA: every action endpoint
with a role lacking the permission → 403. 16 Revocation ≤ 5 s. 17 SoD block. 18 Break-glass audit. 19 Delegated grant cannot exceed the user's permissions or amount limits.
20 Policy simulator matches the real enforcement (property test on random policies).

## Acceptance (G1)
All tests green; `docs/security/access-model.md` with diagrams and examples; P22 BOLA/BFLA suites wired to the real policy.

## Sources
PostgreSQL RLS (T-09); OWASP API Top 10 2023 API1/API3/API5 (T-11); ASVS 5.0 (T-10); Ley 1581 Arts. 5, 6 (S-24) for sensitive data classification.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P04.md` when done)
```
# Handoff P04
Status: DONE | DONE-WITH-BLOCKERS (list BLK ids) | NOT-DONE (reason)
Merged PRs / commits:
Owned paths touched:
Contracts consumed (versions) / CCRs filed (ids, status):
Interfaces provided (+ fakes location):
Tests: counts, coverage %, scenario ids passing, notable property/concurrency/failure-injection tests
Requirements covered (UR ids) and acceptance criteria met (checklist):
Parameters/sources used (ids, statuses) and anything still unverified:
Known limitations, [A] items delivered vs flagged off:
Decisions taken under ambiguity (with rationale):
How to demo (commands, URLs, users):
```
