> **Prompt P14 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 §C; shared/07 §5; shared/11 S8. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P14 — Fixed Assets and Depreciation (multi-book)

## Mission
An asset register with acquisition, capitalization, depreciation per book (NIIF and fiscal), revaluation, impairment, transfers, and disposals, all **posting in-line** (shared/07 §5). Requirement UR-13.

## Owned paths
`backend/assets/**`, `frontend/src/features/assets/**`.

## Dependencies
G1 (P05 multi-book). Provides `AssetService` (`capitalize_from_invoice_line`, `capitalize_from_inventory`) to P11/P09 (publish the fake on day 1).

## Domain
`AssetCategory` (account roles, default methods and lives per book, useful-life ranges, capitalization threshold (parameter: confirm any UVT-based fiscal rule in legal review)), `Asset` (tag/QR, description,
category, company, branch, cost center, custodian employee, location, acquisition date, in-service date, supplier invoice ref, cost components, serial, warranty, insurance, status ∈ {draft, active,
fully_depreciated, disposed}), `AssetBook` (per asset per book: method ∈ {straight_line, declining_balance, sum_of_years [A], units_of_production}, useful life months, salvage value, depreciation start
convention (the following month default; parameter), accumulated, NBV), `Component` (componentization NIIF), `DepreciationRun` (period, book, company; preview → post; idempotent per period+book), `Revaluation`,
`Impairment`, `Transfer`, `Disposal` (sale via P10 invoice, scrap, donation), `PhysicalCount` (scan tags via mobile, discrepancies).

## Rules
* Depreciation computed with Decimal, rounded to the amount precision; the last period absorbs the residual so total = depreciable base; changes in estimates are prospective (NIIF), with an audit.
* Fiscal book uses ET rates/lives as parameters (legal review), and the difference flows to the ledger's `TaxDifferenceRegister`.
* Runs post one summarized entry per book per period by account/dimension, with a per-asset detail sub-ledger. Invariant 5 (GL = register).
* Partial disposal of components; disposal gain/loss; sale of an asset creates a P10 invoice (non-stock line) linked to the disposal.

## API
`/api/v1/assets/categories`, `/assets` (+`:bulk`, `:activate`, `:transfer`, `:dispose`), `/books`, `/depreciation-runs` (+`:preview`, `:post`), `/revaluations` (+`:post`), `/impairments` (+`:post`),
`/physical-counts`, `/reports/register`, `/reports/depreciation-schedule`, `/reports/movement` (opening, additions, disposals, depreciation, closing per category: an NIIF disclosure helper).

## Required tests (T-P14-nn)
01 S8 exact values both books. 02 Residual absorbed in the last period. 03 Run idempotency (posting twice → one entry). 04 Capitalization from an AP invoice rollback. 05 Revaluation/impairment entries. 06 Disposal
gain/loss. 07 Units of production. 08 Change of estimate prospective. 09 GL = register per book. 10 Closed period blocks the run.

## Acceptance (G2)
S8 green; register UI with QR labels PDF.

## Sources
S-42 (NIIF groups, DUR 2420 post-1670/2021), S-10 (ET depreciation rules: verify articles), shared/07 §5.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P14.md` when done)
```
# Handoff P14
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
