> **Prompt P18 · Wave W3 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/07 §9; shared/11 S11. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P18 — Bulk Import/Export (dataio)

## Mission
Fast, safe, resumable bulk import and export for every master and transactional entity (UR-16), used by users, the onboarding agent (P21), the AI agent (P19), and the API, always through the
same service layer and policies (no direct table writes).

## Owned paths
`backend/dataio/**`, `frontend/src/features/dataio/**`.

## Dependencies
G2 (module services); import specs in `contracts/dataio/*.yaml` (P02 drafted; modules refine via CCR).

## Capabilities
* Formats: CSV (UTF-8, `;` or `,` auto-detect, es-CO decimal comma option), XLSX (multi-sheet), JSON Lines; templates downloadable per entity with validation hints and examples; zipped packages for multi-entity loads.
* Pipeline: upload (presigned, virus scan hook) → parse (streaming) → map columns (auto-mapping by header synonyms incl. Spanish, saved mapping profiles; the AI mapping suggestion via P19) → validate (types,
  required, lookups by natural keys: NIT, SKU, account code, employee ID; cross-row rules; P06 NIT DV) → **dry-run** that executes the real service calls inside a transaction that is rolled back,
  producing exact previews (documents, stock moves, journal entries) and an error report → commit in batches (configurable size; each batch atomic; idempotent by `(job_id, batch_no)`; resumable after a crash;
  optional all-or-nothing for small loads) → result report (created/updated/skipped/failed with row numbers and reasons, downloadable as XLSX with error columns).
* Upsert semantics by natural key; explicit modes: create-only, update-only, upsert; never delete via import except for drafts with an explicit mode + approval.
* Transactional imports (opening balances, open AR/AP items, opening stock, historical invoices for reporting) go through dedicated **opening-balance services** in P05/P09/P10/P11 that post
  opening entries against `OPENING_BALANCE_EQUITY` with `source=import` and a "historical" flag (historical invoices are not re-transmitted to DIAN: they are flagged `fiscal_status=external_historical`).
* Export: any list/report/semantic query to CSV/XLSX/JSONL, streaming for large sets, async for > 50k rows with a notification; **field masks and row policies applied**; an export audit event with row counts;
  optional watermark of user/time in XLSX properties.
* Throughput target: ≥ 2,000 rows/s for simple masters and ≥ 500 journal lines/s on the reference machine; 1M-line journal import completes in < 45 min with resumability (measure and record).
* Security: formula injection protection on exports (prefix `'` to cells starting with `= + - @`), file size limits, XML bomb protection for XLSX (defusedxml), zip-slip protection.

## API
`/api/v1/dataio/templates/{entity}`, `/imports` (+`:upload`, `:map`, `:validate`, `:dry-run`, `:commit`, `:cancel`, `:resume`), `/imports/{id}/report`, `/exports` (+`:start`), `/mapping-profiles`.

## Required tests (T-P18-nn)
01 Dry-run creates zero rows and its preview matches the commit results. 02 Resume after killing a worker mid-batch → no duplicates. 03 Batch atomicity. 04 Natural key lookups and DV validation. 05 Opening balances
balanced and sub-ledgers reconcile (S11). 06 Export obeys field masks and row policies (salesperson, auditor). 07 CSV formula injection neutralized. 08 XLSX bomb and zip-slip rejected. 09 Throughput benchmark recorded.
10 Import through the API with a service account respects its permissions.

## Acceptance (G3)
Templates for ≥ 30 entities (parties, items, price lists, CoA, opening balances, open AR/AP, opening stock with lots, employees, contracts, novelties, assets, bank accounts, users/roles,
tax profiles, ICA tariffs, budgets, ...).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P18.md` when done)
```
# Handoff P18
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
