> **Prompt P01 · Wave W0 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-001…006 and ADR-012; shared/07 §7–§9. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P01 — Foundation: repository skeleton, kernel primitives, interfaces

## Mission
Create the backend skeleton and the **kernel** every module depends on: money, ids, unit of work, tenancy context binding, outbox, idempotency, sequences, errors,
flags, clock, base models, and the frozen service **interfaces** (`kernel/interfaces`). Quality here gates the whole build.

## Owned paths
`pyproject.toml`, `uv.lock`, `backend/manage.py`, `backend/erp/**`, `backend/kernel/**`, `Makefile` (targets: dev, test, lint, typecheck, ci, seed-demo, scenarios — P03
may add CI-specific targets via CCR), `.editorconfig`, `.pre-commit-config.yaml`.

## Dependencies
None (Wave 0). Pair with P02: P02 drafts interface signatures in `contracts/`; you implement them as Python `Protocol`s. You may propose changes via CCR.

## Deliverables
1. **Project:** Django 5.2 LTS, DRF, drf-spectacular, psycopg 3, django-environ, celery, structlog, opentelemetry-instrumentation-django/psycopg/celery, hypothesis, pytest-django,
   factory-boy, mypy (strict for `kernel`, `ledger`, `inventory`, `iam`), ruff. Settings split: `base`, `dev`, `test`, `prod`. Prod fails to boot if `DEBUG`, missing secrets,
   `FISCAL_MODE=production` without gate records, or the DB role is a superuser/BYPASSRLS (query `pg_roles` at startup).
2. **`kernel.money`:** `Money(amount: Decimal, currency: str)` immutable; arithmetic only with the same currency; `allocate(total, weights)` with the largest-remainder method
   (deterministic; ties by index); `round_half_even(value, places)`; quantize helpers for the precisions in C2 (`AMOUNT=4`, `QTY=6`, `UNIT_COST=8`, `FX=10`, `PCT=6`).
   Django fields: `AmountField=NUMERIC(20,4)`, `QtyField=NUMERIC(20,6)`, `UnitCostField=NUMERIC(24,8)`, `FxRateField=NUMERIC(24,10)`, `PctField=NUMERIC(9,6)`.
   A ruff/semgrep rule forbids `float(` on money-typed variables and `FloatField` anywhere in `backend/`.
3. **`kernel.ids`:** UUIDv7 generator (monotonic within a ms), `uuid7` default for all PKs.
4. **`kernel.tenancy`:** `TenantContext(tenant_id, company_ids, principal_id, principal_type ∈ {user, service, ai_agent, mcp_client}, on_behalf_of, channel ∈ {ui, api, ai, mcp, import, job})`.
   `bind(ctx)` executes `SET LOCAL app.tenant_id`, `app.company_ids`, `app.principal_id`, and `app.channel` inside the current transaction. A middleware and Celery task base class bind automatically.
   An unbound query on a tenant table returns zero rows (enforced by RLS from P04; you provide the binding and a test helper).
5. **`kernel.models`:** `TenantModel` (tenant_id), `CompanyModel` (tenant_id, company_id, composite FK), `AuditedModel` (created/updated by/at, version for optimistic
   locking with ETag), `ImmutableModel` (DB trigger raising on UPDATE/DELETE of posted rows: generated migration helper `kernel.db.immutable_trigger(table, posted_column)`).
6. **`kernel.uow`:** `unit_of_work(ctx, idempotency_key=None)` context manager. It opens `transaction.atomic()`, binds tenancy, handles the idempotency lookup and insert
   (table `kernel_idempotency(tenant_id, key, request_hash, response, status, created_at)`, unique `(tenant_id, key)`; if the same key has a different hash → 422), collects domain events,
   writes them to `kernel_outbox` at commit, and retries on serialization failure/deadlock (SQLSTATE 40001/40P01) up to 3 times with jitter. External I/O inside the UoW is
   forbidden: a guard raises if `requests/httpx` is called while a UoW is active (test).
7. **`kernel.outbox`:** table, relay command (`manage.py outbox_relay`) with `FOR UPDATE SKIP LOCKED` batches, publish to Celery by event type routing, a consumer dedupe table
   `kernel_inbox(consumer, event_id)`, dead letters, metrics.
8. **`kernel.sequences`:** gap-free sequences per `(tenant, company, sequence_code, prefix)` allocated inside the UoW with `SELECT ... FOR UPDATE`. Supports DIAN numbering
   ranges: `range_from`, `range_to`, `valid_from/to`, `resolution_number`, `technical_key`, exhaustion alerts at 80/95%. Numbers are assigned only inside the server UoW (C1-12). Per-terminal block pre-allocation is an optional extension point used solely by the `[A]` flag `pos_offline_fiscal_selling` (ADR-015); it is off by default and needs no `[P]` implementation.
9. **`kernel.errors`:** a domain exception hierarchy mapped to RFC 9457 problem+json, with codes registered in `contracts/errors.yaml`.
10. **`kernel.flags`:** feature flags per tenant/company with defaults from code; `[A]` features are registered here.
11. **`kernel.clock`:** injectable clock (tests freeze time); business dates in the company timezone (America/Bogota) and storage in UTC.
12. **`kernel.parameters` interface:** the `ParameterProvider.get(code, at_date, company) -> ParameterValue(value, status, source_id)` protocol (P06 implements). It raises
    `ParameterUnknown` for UNKNOWN or missing values, and `ParameterNotVerified` in production when the value lacks a legal-review stamp (DISPUTED values compute with a warning snapshot; see C1-9).
13. **`kernel/interfaces/*.py`** (frozen at G1, drafted at G0): `PostingService`, `StockService`, `TaxService`, `PolicyService`, `AuditService`, `WorkflowService`, `SchedulerService`,
    `FiscalGateway`, `DocumentRenderer`, `ReportQueryService`, `NotificationService`, `FileStore`. Each has a docstring contract (pre/postconditions, errors, idempotency).
14. **Demo tenant loader** command skeleton `seed_demo` (P21 fills it with data).

## Required tests (T-P01-nn)
01 Money arithmetic/allocation property tests (sum preserved, deterministic). 02 Field precision round-trip with PostgreSQL. 03 UUIDv7 monotonic and sortable. 04 UoW atomicity:
an exception after an outbox write leaves no rows. 05 Idempotency: same key and same body → same response; different body → 422; concurrent same key → one execution.
06 Sequence gap-free under 50 concurrent allocations. 07 Deadlock retry works (two transactions locking in reverse order, simulated). 08 External I/O guard. 09 Prod boot refuses
superuser/BYPASSRLS. 10 Tenancy binding is cleared after the transaction (pooled connection reuse). Coverage for `kernel` ≥ 95% branches.

## Acceptance (G0 contribution)
`make ci` green; kernel documented in `docs/kernel.md` with examples; interfaces reviewed by P02 and P00.

## Sources
PostgreSQL row security and roles (T-09); Django supported versions (T-07); RFC 9457 (T-14).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P01.md` when done)
```
# Handoff P01
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
