> **Prompt P11 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 R-FE-08, R-FE-10, R-TX-03, R-TX-04; shared/07 §2; shared/11 S2, S3, S9, S12. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P11 — Purchasing, Accounts Payable, Documento Soporte, Landed Cost

## Mission
Procure-to-pay with three-way match, **receipts updating stock and valuation in-line**, vendor invoices posting in-line, documento soporte for non-obligated suppliers, landed costs, AP and
payment proposals, and RADIAN acceptance events on received invoices. Requirements UR-10, UR-14, UR-15.

## Owned paths
`backend/purchasing/**`, `frontend/src/features/purchasing/**`.

## Dependencies
G1; `StockService`, `PostingService`, `TaxService`, `PartyService` (P10 fake → real), `FiscalGateway` (P15), P14 capitalization interface (`AssetService.capitalize_from_invoice_line`; P14 publishes a fake).

## Documents and flows
* `PurchaseRequisition` (from users, reorder job, AI) → approval → `RFQ` (multiple vendors, comparison table) → `PurchaseOrder` (approval by amount, blanket `[A]`) → `GoodsReceipt` (partial, over-receipt
  tolerance, lots/serials, quality hold `[A]`) → `VendorInvoice` (matched to PO/receipt lines; tolerances on price and qty with approval on breach; services and expenses without a PO allowed with an approval policy)
  → `VendorCreditNote` → `PaymentProposal` (due date, discount date, priority, bank) → approval → payment file (P16 adapters) → payment posting.
* **Receiving e-invoices:** ingest supplier e-invoices (XML + AttachedDocument) from a monitored mailbox or an upload, validate the signature, CUFE, and buyer NIT, pre-fill the vendor invoice by matching the PO,
  detect duplicates (supplier NIT + number + CUFE), and generate RADIAN events (acuse de recibo, recibo del bien, aceptación expresa, reclamo) through P15. Tacit acceptance is tracked as a timer
  (3 business days following receipt without a claim: verified in Res. 085/2022, R-FE-10; event codes from the annex, B-09).
* **Documento soporte (R-FE-08):** for purchases from persons not obligated to invoice: per operation or weekly accumulation per supplier (a scheduler job on the last business day of the week),
  nota de ajuste within the firmeza term, CUDS via P15.
* **Landed cost:** a `LandedCost` document linking cost invoices (freight, insurance, customs duties/DIAN import declarations as manual data) to receipts, allocation basis (value, qty, weight, volume, manual),
  posting per shared/07 §2 through `StockService.revalue`.
* **Withholding computation** via P06 (renta, ReteIVA, ReteICA) with the explanation trace; withholding certificates issued to suppliers (P17 renders).
* **AP:** open items, aging, advances, offsets of AR/AP with the same party (cruce de cuentas, approval), vendor statement reconciliation, GRNI report.
* Fixed-asset purchases: an invoice line with an asset category calls P14 to create/capitalize the asset in the same UoW.
* Imports in foreign currency with the TRM.

## API
`/api/v1/purchasing/requisitions` (+`:approve`, `:to-rfq`, `:to-po`), `/rfqs` (+`:award`), `/orders` (+`:approve`, `:send`, `:close`), `/receipts` (+`:validate`), `/vendor-invoices`
(+`:match`, `:post`, `:simulate`), `/vendor-credit-notes` (+`:post`), `/support-documents` (+`:post`, `/adjustment-notes`), `/landed-costs` (+`:allocate`, `:post`), `/received-einvoices`
(+`:ingest`, `:accept`, `:claim`), `/ap/open-items`, `/ap/aging`, `/payment-proposals` (+`:approve`, `:generate-file`), `/payments` (+`:post`), `/offsets` (+`:post`), `/reports/grni`.

## Events
`purchasing.receipt.validated.v1`, `purchasing.vendor_invoice.posted.v1`, `purchasing.support_document.posted.v1`, `purchasing.payment.posted.v1`, `purchasing.einvoice.received.v1`.

## Required tests (T-P11-nn)
01 S2 exact entries incl. the price difference. 02 Three-way match tolerances and the approval on breach. 03 S3 landed cost. 04 Vendor return at the original layer cost. 05 S9 documento soporte weekly accumulation with a
holiday Friday. 06 Received e-invoice ingestion: valid, duplicate, wrong buyer NIT, bad signature. 07 RADIAN events emitted via the fake gateway; the tacit acceptance timer. 08 Payment proposal + SoD (creator of
the vendor bank account cannot approve). 09 AP = GL and GRNI = receipts not invoiced (invariant 4). 10 FX payable S12. 11 Asset capitalization from an invoice line in the same transaction (rollback test). 12 Offsets AR/AP.

## Acceptance (G2)
S2, S3, S9, S12 green; UI for requisition-to-payment with the match screen.

## Sources
S-04 (Res 167/2021), S-06 (RADIAN, tacit acceptance), S-09 (withholding), S-10 (ET 437-1 ReteIVA), BM-01/BM-02 (GRNI practices).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P11.md` when done)
```
# Handoff P11
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
