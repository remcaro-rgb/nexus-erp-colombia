> **Prompt P10 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 R-FE-01…R-FE-05, R-TX-04; shared/07 §1; shared/11 S1, S10. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P10 — Sales, Invoicing, Accounts Receivable and CRM

## Mission
Lead-to-cash: CRM pipeline, quotes, sales orders, deliveries, **invoices posting in-line to the GL and stock**, credit/debit notes, receipts, and AR with Colombian withholdings, plus fiscal
document hand-off to P15. Requirements UR-03, UR-09, UR-14.

## Owned paths
`backend/sales/**`, `backend/crm/**`, `frontend/src/features/{sales,crm}/**`.

## Dependencies
G1; `StockService` (P09 fake → real), `TaxService` (P06), `PostingService` (P05), `FiscalGateway` interface (P15 fake → real), workflow (P07).

## CRM [P]
`Lead`(source, campaign, contact data with consent record ref, status, owner, score), `Contact`, `Account` (= customer party; links to the Party master owned here: `Party` is shared by
sales/purchasing with roles customer/supplier/employee-link; NIT+DV, person type, tax profile ref (P06), addresses with DANE municipality, emails for e-invoice delivery, payment terms, credit limit,
price list, salesperson, territory), `Opportunity`(pipeline, stage, amount, probability, expected close, products), `Activity`(call, meeting, task, email logged; due dates; reminders via P07), Kanban
pipeline UI, forecast by stage/owner, customer 360 page (orders, invoices, AR aging, activities, tickets `[A]`), and duplicate detection by NIT/email/phone. Email integration `[A]` (logging through IMAP/Graph adapters).
Campaigns and commissions `[A]`.
**Party ownership note:** `Party` lives in `backend/sales/party/` (owned by P10) and is exposed through `PartyService` for P11/P13/P16.

## Sales documents
* `Quote` → `SalesOrder` (confirm reserves stock; credit check: block or require approval when over the limit/overdue) → `Delivery` (pick/pack/ship; `issue` at cost → COGS posting) → `Invoice`
  (from orders or deliveries, or direct with deliver-on-invoice) → `CreditNote`/`DebitNote` (linked to the invoice, reason codes per the annex: devolución, anulación, rebaja, descuento, ajuste de precio, other).
* Price lists (by customer group, currency, validity, qty breaks), discounts (line/document, approval above threshold), promotions `[A]`.
* Invoice posting (one UoW): number from the DIAN range (kernel.sequences), taxes via P06, stock issue when applicable, journal via P05 per shared/07 §1, AR open item, outbox event
  `sales.invoice.posted.v1` → P15 builds/signs/transmits **asynchronously** (no external call inside the transaction). The fiscal status lifecycle is shown on the document.
* Representación gráfica PDF (P17 renderer with a template including CUFE, QR, resolution text, "valor en letras").
* Export invoices (USD, exempt) and the TRM at the invoice date.
* AR: open items, partial payments, application of advances and credit notes, receipts with withholdings certified by customers (shared/07 §1), aging, statements, dunning `[A]`, write-off with approval,
  customer withholding certificate reconciliation (match the certificates received vs withholdings recorded).
* Returns (RMA): receipt back at the original cost + credit note.
* Recurring invoices (subscriptions) via the scheduler.

## API
`/api/v1/crm/leads` (+`:convert`), `/contacts`, `/opportunities` (+`:win`, `:lose`), `/activities`, `/pipelines`, `/forecast`; `/api/v1/sales/parties` (+`:bulk`, `:merge`), `/price-lists`,
`/quotes` (+`:send`, `:accept`, `:to-order`), `/orders` (+`:confirm`, `:cancel`, `:to-invoice`), `/deliveries` (+`:validate`), `/invoices` (+`:post`, `:simulate`, `:send`), `/credit-notes`
(+`:post`), `/debit-notes` (+`:post`), `/receipts` (+`:post`), `/ar/open-items`, `/ar/aging`, `/ar/statements`, `/write-offs` (+`:post`), `/returns`.

## Events
`crm.lead.converted.v1`, `crm.opportunity.won.v1`, `sales.order.confirmed.v1`, `sales.delivery.done.v1`, `sales.invoice.posted.v1`, `sales.credit_note.posted.v1`, `sales.receipt.posted.v1`.

## Required tests (T-P10-nn)
01 S1 end-to-end exact journal lines. 02 Credit limit blocking and approval. 03 Credit note with return at the original cost. 04 Price-discount credit note without stock. 05 Receipt with three withholdings.
06 Partial payments and advances application; AR = GL (invariant 4). 07 FX invoice and the realized difference. 08 Invoice posting atomic with stock and GL (failure injection in the tax step). 09 Fiscal rejection path: the
document stays posted, the status is rejected, and the corrective flow works (no automatic GL reversal). 10 Lead conversion creates the party with consent. 11 Pipeline forecast. 12 Salesperson row policy (own customers). 13 Margin
fields hidden for salespeople across channels. 14 Duplicate party detection by NIT+DV. 15 Simulation endpoint returns entries without persisting.

## Acceptance (G2)
S1 and S10 (IC sell side) green; UI for the CRM pipeline, customer 360, quotes/orders/invoices with the document pattern and accounting preview.

## Sources
R-FE-01/03/05 (S-01, S-02, S-03); shared/06 sales/CRM practices; shared/07 §1.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P10.md` when done)
```
# Handoff P10
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
