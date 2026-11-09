> **Prompt P12 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/01 C1-12; shared/03 ADR-015; shared/04 R-FE-03, R-FE-05, R-FE-07; shared/07 §1; shared/09 EG-13; shared/11 S4. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P12 — Point of Sale (PWA, online finalization, DEE POS electrónico)

## Mission
A fast touch/keyboard POS for retail counters, with cash sessions, multiple tenders, and returns. Every completed sale, return, and cash movement is finalized only by a
successful **server transaction that posts GL and stock in-line** (C1-1, C1-2, C1-12, ADR-015). It also covers the DIAN equivalent document POS electronic (R-FE-07), conversion
to an electronic invoice on request, and DIAN/PT contingency handling while our server is online. A lost connection to our server never produces a completed sale.

## Owned paths
`backend/pos/**`, `frontend/src/features/pos/**`.

## Dependencies
G1; P10 `SalesPostingService` (sale posting rules reused; P10 publishes a fake), P09 `StockService`, P15 `FiscalGateway`, P08 PWA primitives (read cache, draft store, connectivity hook).

## Domain
`PosConfig`(company, branch, warehouse, price list, allowed tenders, receipt printer profile, DIAN numbering range for DEE POS, cash limits),
`Terminal`(device registration with a device key; **no** pre-allocated number blocks in `[P]`: numbers are assigned server-side by `kernel.sequences` at commit), `CashSession`(opening float, cashier, state ∈
{open, closing, closed}, expected vs counted by tender, blind count option, difference posting per shared/07 §1, Z report), `Ticket`(lines, discounts with permission, tenders, customer (default final consumer
per annex rules), fiscal doc type ∈ {DEE_POS, FE when requested}, client UUID used as the Idempotency-Key), `DraftCart` (client-side only, unposted, no number), `CashMovement` (pay-in/pay-out with reason, approval),
`Return` (referencing the original ticket, refund tender rules), `Tender` (cash, card with external terminal reference, transfer/QR, voucher, mixed).

## Behavior
* **Checkout (the only way to finalize):** `POST /pos/tickets:checkout` (Idempotency-Key = client UUID) → one UoW: number, taxes (P06), stock issue, GL, AR/cash, outbox for DEE
  transmission → response < 300 ms p95. The receipt prints **only after** the commit response, and it shows the committed number and the CUDE/QR when P15 provides them.
* **Connectivity loss (our server unreachable):** the connectivity hook switches the UI to a blocking "Sin conexión: no se pueden finalizar ventas" banner. Allowed: browsing the cached
  catalog/prices (read-only, encrypted IndexedDB) and building or parking carts as **unposted drafts** watermarked "BORRADOR – sin valor fiscal". **Disabled:** checkout, tender capture,
  fiscal numbering, CUDE computation, fiscal receipt printing, returns, cash movements, and session close. Nothing is queued for later posting.
* **Reconnect:** a parked draft is resubmitted through the normal checkout. The server recomputes prices, taxes, and stock. Any difference from the draft is shown, and the cashier must confirm
  before commit. Negative-stock policy applies as for any sale.
* **DIAN/PT outage with our server online:** checkout commits in-line as usual, and P15 handles the DEE in contingency (R-FE-05, 48 h timer). A contingency banner is shown.
* Convert to FE: buyer data (minimum per R-FE-03) → FE issued and the POS document handled per annex rules (verify in the annex whether a DEE already issued must be annulled with a note; implement per
  annex, flag B-06).
* Hardware: ESC/POS printing via WebUSB/WebSerial or a local print bridge `[A]`; barcode scanners as keyboard input; customer display `[A]`; scale `[A]`.
* UX: large touch targets (WCAG 2.5.8), keyboard shortcuts (F-keys), product grid by category, quick search, a hold/park ticket (unposted), split tender, a receipt reprint (marked as copy).
* Cash session close: count by denomination, expected vs counted per tender, difference posting, Z report PDF, deposit to safe/bank.
* **`[A]` `pos_offline_fiscal_selling`** (ADR-015, EG-13): disconnected fiscal selling with per-terminal number blocks, local CUDE, caps, and GL/stock posted at sync. It is **disabled by default**,
  optional to build, never part of `[P]` acceptance, and never used to pass required tests. If built, it must have its own tests, behind the flag, plus a startup check that refuses the flag in
  production without the EG-13 evidence file.

## API
`/api/v1/pos/configs`, `/terminals` (+`:register`), `/sessions` (+`:open`, `:close`), `/tickets` (+`:checkout`), `/returns` (+`:post`), `/cash-movements`,
`/tickets/{id}:to-invoice`, `/reports/z`, `/catalog-snapshot` (delta read-sync with ETag; read-only).

## Required tests (T-P12-nn)
01 S4 full scenario. 02 Checkout atomicity (failure injection at the stock step → nothing persisted). 03 Idempotent checkout (the same Idempotency-Key twice → one ticket). 04 Disconnected checkout is blocked:
with the server unreachable, the checkout, tender, return, cash-movement, and print actions are disabled, and the server shows 0 new entries/moves/numbers. 05 Reconnect commit: a parked draft resubmits and commits atomically;
a server price/tax change is surfaced and needs confirmation. 06 No client code path assigns fiscal numbers or computes CUDE when `pos_offline_fiscal_selling` is off (static check + test). 07 DIAN-down contingency with the server online:
in-line commit plus a 48 h timer and alert. 08 Return referencing the original ticket at the original cost. 09 Cash difference posting. 10 Cashier cannot see costs or other sessions (row/field policy).
11 Playwright: network off → build and park a cart, checkout disabled, banner shown; network on → checkout commits; the receipt prints only after commit. 12 Performance: add-to-cart < 100 ms; checkout p95 < 300 ms.
13 The production startup refuses `pos_offline_fiscal_selling=on` without the EG-13 evidence.

## Acceptance (G2)
S4 green with `pos_offline_fiscal_selling` off; a POS PWA installable; docs `docs/modules/pos.md` explaining the online-finalization boundary and the `[A]` exception honestly.

## Sources
R-FE-05/07 (S-01), R-FE-03 (S-03), ADR-015, C1-12, EG-13.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P12.md` when done)
```
# Handoff P12
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
