> **Prompt P05 · Wave W1 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-005, ADR-006; shared/04 §C; shared/07 (all); shared/11 S1–S12 (journal expectations). Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P05 — General Ledger, Posting Engine, Periods, Multi-book, FX

## Mission
Build the accounting core that every money process posts to **in-line** (UR-14): chart of accounts, account determination, the posting engine, journals, periods and closing,
multi-book (NIIF + fiscal), dimensions, FX, and financial statements foundations.

## Owned paths
`backend/ledger/**`, `frontend/src/features/ledger/**`.

## Dependencies
G0. Provides `PostingService` (kernel/interfaces) to P09–P16. Publish `ledger/testing/fakes.py` (an in-memory balanced-entry recorder with the same validation) on day 1.

## Domain model
* `Account`(company, code, name, type ∈ {asset, liability, equity, income, expense, memo}, nature debit/credit, level, parent, is_control (AR/AP/inventory/etc. → only sub-ledgers post),
  reconcilable, currency_restriction, requires_dimensions[], active, PUC mapping, taxonomy tags).
* `AccountRole` (logical roles from shared/07) and `AccountDetermination` rules: priority-ordered matching on company, document type, item category, warehouse, party group, tax code, dimension → account.
* `Book` (NIIF, FISCAL, plus `[A]` others); `Journal` (sales, purchases, cash, bank, payroll, inventory, assets, adjustments, opening, closing); `JournalEntry`(company, book, journal,
  number via kernel.sequences, date, period, status ∈ {draft, posted, reversed}, source DocumentRef, reversal_of, memo, currency, fx_rate, created_by, posted_by, channel);
  `JournalLine`(account, debit, credit, amount_currency, currency, party, dimensions: branch, cost_center, project, item, employee, asset, plus a JSONB extra, tax_code, tax_base, due_date, reconcile_key).
* `FiscalYear`, `Period` (monthly + adjustment period 13), status per module ∈ {open, soft_closed (only role X posts), closed}; `PeriodReopenRequest` (approval, SoD, audit).
* `ExchangeRate`(currency, date, rate, source: TRM adapter, manual with approval).
* `TaxDifferenceRegister` (NIIF vs fiscal by account/asset: temporary or permanent, for conciliación fiscal ET 772-1, formato 2516/2517 support `[A]` export).
* `RecurringTemplate`, `AccrualSchedule` (auto-reversal), `Budget` (by account × dimension × period).

## Posting engine behavior
`PostingService.post(request: PostingRequest) -> JournalEntryRef`:
1. Must be called inside an active UoW (it asserts this). It resolves account roles via determination; it fails with `account_determination_missing` (never a default "suspense" account unless the
   company explicitly configures a suspense role, which creates an alert).
2. Builds lines; converts to company currency with the date's rate; rounds (shared/07 §8); adds rounding lines; validates invariant 1 per currency and COP; checks the period status per module and
   book; checks control-account rules (only the sub-ledger source types can post to control accounts); checks required dimensions.
3. Persists the entry with `status=posted` and its immutability trigger; emits `ledger.entry.posted.v1`.
4. Multi-book: a posting request can contain book-specific line sets (for example depreciation). If not specified, lines post to all books flagged "mirror" (the default for operational documents).
5. `reverse(entry_id, date, reason)` creates a mirrored entry; the original is marked reversed (status only, the lines untouched). Partial reversal is not allowed; use adjustments.
6. `simulate(request)` returns the entry without persisting (used by `:simulate` endpoints and the AI what-if).
7. Performance: posting a 200-line entry < 150 ms p95 on the reference machine; balances are maintained via `ledger_balance(company, book, account, period, dims_hash)` incremented in-line
   (same transaction, row lock) and verified nightly against the lines (P22 reconciliation job).

## Processes and UI
CoA management (tree with import, the PUC template from P21), manual journal entries (spreadsheet-like grid, keyboard-first, balance indicator, attachments, approval if over threshold), recurring entries,
accruals with auto-reversal, FX revaluation (open items AR/AP/bank by currency; shared/07 §6), period close cockpit (checklist: sub-ledger reconciliations, unposted drafts, GRNI review,
depreciation run done, payroll posted, FX revaluation, tax review; then lock), year-end closing entry generation, opening balances (used by P21), account reconciliation (bank/AR/AP clearing matching),
GL inquiry with drill-down to the source document, trial balance (by book, with comparison), general ledger, journal book (libro diario), libro mayor y balances, auxiliary by party (auxiliar por tercero:
very common in Colombia), balance sheet and P&L foundations (P17 builds the full reporting UI), budget vs actual.

## API
`/api/v1/ledger/accounts`, `/account-roles`, `/account-determinations`, `/books`, `/journals`, `/entries` (+`:post`, `:reverse`, `:simulate`), `/periods` (+`:soft-close`, `:close`,
`:request-reopen`, `:approve-reopen`), `/exchange-rates`, `/fx-revaluations` (+`:run`), `/recurring-templates`, `/accruals`, `/budgets`, `/reports/trial-balance`, `/reports/general-ledger`,
`/reports/party-ledger`, `/reconciliations`, `/year-end-close:run`, `/tax-differences`.

## Events
`ledger.entry.posted.v1`, `ledger.entry.reversed.v1`, `ledger.period.status_changed.v1`.

## Required tests (T-P05-nn)
01 Hypothesis: random posting requests → always balanced or rejected. 02 Rounding lines bounded by tolerance. 03 Period closed → reject; soft-closed → only permitted role. 04 Immutability trigger
(UPDATE/DELETE on posted rows fails at the DB). 05 Reversal correctness. 06 Control accounts reject manual lines. 07 Atomic rollback: a module UoW raising after `post()` leaves no entry or balance change.
08 Concurrency: 100 parallel postings to the same account keep balances correct. 09 FX revaluation per S12. 10 Year-end close zeroes P&L into retained earnings. 11 Balance table = lines (property test).
12 Multi-book depreciation entries land in the correct books. 13 Determination priority and missing-rule errors. 14 Trial balance equals the sum of balances per book. 15 Simulation does not persist anything (row counts unchanged).

## Acceptance (G1)
Tests green; invariants 1, 2, 8 enforced; `docs/modules/ledger.md`; UI for CoA, entries, periods, and trial balance with a11y checks.

## Sources
DUR 2420/2015 groups post-1670/2021 (S-42), Ley 962/2005 Art. 28 retention (S-12), ET 632/714/772-1 (S-10), BM-06/BM-06b SAP universal journal (ACDOCA), BM-03 consolidation (for P16 compatibility).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P05.md` when done)
```
# Handoff P05
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
