> **Prompt P02 · Wave W0 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 (ADR headings and decisions); shared/07 §9; shared/08. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P02 — API, Event, Permission and Interface Contract Freeze

## Mission
Produce the **frozen contracts** that let 20+ agents work in parallel without colliding: OpenAPI 3.1, event schemas, the permission catalog, the error registry,
field classifications, import/export specs, and the interface drafts. You are the guardian of "every process is available via the API" (UR-22).

## Owned paths
`contracts/**` (module fragments become module-owned after G0), `tests/contract/**`.

## Inputs
All module prompts P04–P21 (read every "API" and "Events" section), shared/01–07.

## Deliverables
1. `contracts/openapi/root.yaml` with: servers, security schemes (OAuth2 authorization code + PKCE, client credentials for service accounts, and token exchange for delegated agents
   RFC 8693), shared components (Money, Quantity, DocumentRef, PartyRef, AuditStamp, Page {items, next_cursor}, Problem), parameters (`Idempotency-Key`, `If-Match`,
   `X-Company-Id`, `fields`, `filter` grammar `field[op]=value` with ops eq, ne, lt, lte, gt, gte, in, contains, between, isnull; `sort`; `cursor`; `limit` ≤ 500).
2. **Module fragments** `contracts/openapi/modules/<module>.yaml` for every endpoint listed in P04–P21, using the conventions:
   * Resources: `/api/v1/<module>/<resource>`; actions as sub-resources: `POST /api/v1/sales/invoices/{id}:post`, `:void`, `:approve`, `:send`.
   * Every state change is an explicit action endpoint with `Idempotency-Key` required; no state change via PATCH of a `status` field.
   * Long-running operations return `202` + `/api/v1/operations/{id}` (status, progress, result links).
   * Bulk endpoints: `POST /api/v1/<module>/<resource>:bulk` (max 1,000 items, per-item results, all-or-nothing flag).
   * Simulation endpoints: `POST .../:simulate` returns the would-be documents, journal entries, and stock moves without persisting (C1-3).
3. **Events** `contracts/events/<module>/<event>.v1.json` (JSON Schema 2020-12) using the envelope from shared/02 §2, for example `sales.invoice.posted.v1`, `inventory.move.done.v1`,
   `ledger.entry.posted.v1`, `einvoice.document.status_changed.v1`, `payroll.run.posted.v1`, `iam.policy.changed.v1`.
4. **Permission catalog** `contracts/permissions/catalog.yaml`: every endpoint mapped to `<module>.<resource>.<action>`; field classifications for every schema property
   (`x-classification`); and default role templates (Administrador, Contador, Auxiliar contable, Vendedor, Cajero POS, Jefe de bodega, Comprador, Analista de nómina,
   Empleado (self-service), Gerente, Auditor (read-only), Agente IA (delegated only)).
5. **Errors** `contracts/errors.yaml` with stable codes (`insufficient_stock`, `period_closed`, `parameter_unknown`, `fiscal_rejected`, `policy_denied`, `sod_violation`, ...).
6. **Import/export specs** `contracts/dataio/<entity>.yaml` (columns, types, required, lookups by natural key such as NIT or SKU, validation rules) for every importable entity listed in P18.
7. **Interface drafts** for `kernel/interfaces` (coordinate with P01).
8. **Contract tests:** schema lint (spectral ruleset in `contracts/.spectral.yaml` enforcing the conventions above); a test that the Django URL conf and generated schema match
   the contracts (drift detector, used from W1); a test that every operation has a permission and every schema property has a classification; JSON Schema validation for event fixtures.
9. **Mock server** (Prism or similar, containerized) so the frontend and MCP can start before the backend.
10. **Versioning policy** `contracts/VERSIONING.md`: SemVer; additive changes are minor; breaking changes need a CCR + `v2` path or event version; deprecation headers (`Deprecation`, `Sunset`).

## Required tests
T-P02-01 spectral lint clean. 02 Every UI-facing process in P08–P21 appears as an operation (checklist generated from prompts). 03 Every operation has a permission code. 04 Every
property has a classification. 05 Event fixtures validate. 06 Drift detector fails on an intentionally drifted fixture.

## Acceptance (G0)
Contracts tagged `contracts-v1.0.0`; mock server in compose; P00 sign-off.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P02.md` when done)
```
# Handoff P02
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
