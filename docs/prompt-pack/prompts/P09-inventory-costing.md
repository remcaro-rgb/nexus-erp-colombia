> **Prompt P09 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-008; shared/07 §3, §7, §9; shared/11 S2, S3, S5. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P09 — Inventory, Warehousing and Costing (in-line valuation)

## Mission
Implement items, warehouses, stock moves, and valuation so that **every stock process updates inventory and valuation in-line** and posts to the GL through `PostingService` in the same
transaction (UR-08, UR-15; ADR-008; shared/07 §3, §7).

## Owned paths
`backend/inventory/**`, `frontend/src/features/inventory/**`.

## Dependencies
G1 (ledger, tax, iam, workflow). Provides `StockService` to P10, P11, P12, P14, P16. **Day 1:** publish `inventory/testing/fakes.py` that implements the interface with in-memory
qty and the WAC/FIFO math (it shares the pure valuation functions module `inventory/valuation/core.py` with the real implementation, so fakes and the real service can't diverge).

## Domain model
`Item`(sku, name, type ∈ {stockable, consumable, service, kit}, category (account determination + valuation method), base UoM, UoM conversions, barcode(s), tax profile (VAT/INC codes),
tracking ∈ {none, lot, serial}, expiry, dimensions/weight/volume, reorder min/max per warehouse, default supplier(s), active, custom fields, DIAN product code standard (annex: UNSPSC/GTIN/own)),
`ItemVariant` `[A]`, `Warehouse`(company, branch, address, municipality, allow_negative, valuation_scope), `Location/Bin`, `Lot`(number, expiry), `Serial`, `StockMove`(company, item, from_location,
to_location, qty in base UoM, uom entered, unit_cost, value, lot/serial, source DocumentRef, state ∈ {draft, reserved, done, cancelled}, done_at, posted entry ref) — **append-only once done**;
`StockQuant`(item, location, lot) materialized on-hand + reserved, updated in-line; `ValuationLayer`(company, item, [warehouse], move, qty, unit_cost, value, remaining_qty, remaining_value);
`ItemValuation`(company, item, [warehouse], qty, value, avg_cost) as the lockable row; `Transfer` (with in-transit), `InventoryCount` (cycle/full, blind option, freeze snapshot, recount, approval),
`Adjustment`, `Scrap`, `Kit/BOM` (kits consumed at sale), `ReorderRule`, `LandedCost` (P11 creates; P09 applies revaluation), `Reservation`.

## StockService (interface; all calls inside the caller's UoW)
`receive(lines, source, date) -> moves` (creates layers; WAC/FIFO update), `issue(lines, source, date, cost_policy="current"|"original_of"(ref)) -> moves with cost`, `transfer(...)`, `adjust(...)`,
`reserve(...)`, `unreserve(...)`, `revalue(item, delta_by_layer|amount, source)` (price differences and landed cost: shared/07 §2), `cost_of(item, qty, date)` (for previews/simulation), `available(item, warehouse)`.
Each returns the moves and the posting lines that the caller passes into the same `PostingService.post()` or posts itself with a stock-only document (adjustments).

## Rules
* Locking order and retries per shared/07 §7. Negative stock blocked unless allowed (ADR-008) and then the correction posts in-line on receipt.
* Back-dating limits per shared/07 §7 (default safe disallow).
* UoM conversion uses exact decimals; rounding of qty to the UoM precision; cost per base unit.
* Lots/serials: FEFO suggestion on issue; serial uniqueness per item; recall report (forward and backward traceability).
* Reorder: nightly job proposing purchase requisitions (P11 interface) or transfers; AI can explain the suggestions.
* Valuation report as of any date (from layers), stock ledger (kardex: very common in Colombia, by item with qty in/out/balance and value), aging by lot, slow-moving, ABC analysis.

## API
`/api/v1/inventory/items` (+ `:bulk`), `/uoms`, `/warehouses`, `/locations`, `/lots`, `/serials`, `/moves` (read-only + `:cancel` for drafts), `/quants`, `/transfers` (+`:ship`, `:receive`),
`/counts` (+`:freeze`, `:submit`, `:approve`), `/adjustments` (+`:post`), `/scraps` (+`:post`), `/reorder-rules`, `/reorder:run`, `/reports/kardex`, `/reports/valuation`, `/reports/availability`,
`/reports/traceability`, `/kits`.

## Events
`inventory.move.done.v1`, `inventory.valuation.changed.v1`, `inventory.stock.below_min.v1`, `inventory.count.approved.v1`.

## Permissions/field policy
Cost and valuation fields are classified `confidential` (hidden for sales and POS roles by default). Warehouse-scoped row policy for warehouse staff.

## Required tests (T-P09-nn)
01 WAC math (S2) incl. the residual-on-zero. 02 FIFO layers (S2 variant). 03 Price-difference revaluation split between on-hand and COGS (S2 step 5). 04 Landed cost allocation by value/qty/weight/volume
with residual rounding (S3). 05 Returns at the original cost. 06 Negative stock disallowed → 409, no partial writes. 07 Concurrency S5 (barrier threads; exactly one succeeds). 08 Failure injection after the move
insert → full rollback incl. GL. 09 Deadlock-free ordering under random multi-line documents (stress test 200 parallel docs). 10 Invariant 3 (GL inventory = layers) after random sequences (hypothesis
stateful test). 11 Count with variance posts the adjustment. 12 Transfer in-transit. 13 Lot FEFO and serial uniqueness. 14 Kardex totals = valuation report. 15 Fakes and the real service produce identical
results on the shared test vectors.

## Acceptance (G2)
Tests green; S2, S3, S5 green with the real P05; UI: items, warehouses, stock moves, counts, kardex, and valuation with the document pattern.

## Sources
BM-01 ERPNext perpetual inventory, BM-02 Odoo valuation; shared/07.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P09.md` when done)
```
# Handoff P09
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
