> **Prompt P16 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/06 (Finance, cross-cutting); shared/07 §6; shared/11 S10, S12. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P16 — Treasury, Banking, Intercompany and Consolidation

## Mission
Bank accounts, cash management, bank statement import and reconciliation, payment files, the TRM rates feed, intercompany automation between companies of the same tenant, and consolidation `[A]`.

## Owned paths
`backend/treasury/**`, `backend/consolidation/**`, `frontend/src/features/{treasury,consolidation}/**`.

## Dependencies
G1; P05, P10 (receipts), P11 (payments), P13 (payroll payments).

## Treasury [P]
* `BankAccount` (company, bank, number (secret, encrypted, masked last 4), type, currency, GL role, GMF-exempt flag), `CashBox`, `BankTransfer` (between own accounts with transit), `BankStatement` + lines
  (import adapters: CSV/Excel generic mapping, OFX/CAMT.053 `[A]`, per-bank formats on EG-5), `ReconciliationRule` (match by amount/date window/reference/party regex), the **auto-match** engine + an AI suggestion hook (P19
  can propose matches; a human confirms), manual matching UI (split/merge), fees and GMF posting, an unreconciled items report.
* Payment files: an adapter interface `BankFileFormat` with a generic CSV implementation and per-bank adapters `[A]` (formats pending EG-5); payment batch approval with SoD (P04) and 2-person approval over thresholds.
* Cash-flow position by bank and forecast from AR/AP due dates `[A]`.
* TRM feed: an adapter for the official daily representative market rate (Superintendencia Financiera / Banco de la República; verify the official endpoint at build time) with manual override + approval; stored in `ledger.ExchangeRate`.
* GMF (4×1000) computation on outflows as a parameter-driven tax code (P06) with the exemption flag (R-BK-02, legal review).

## Intercompany [P]
* `IntercompanyAgreement` between company pairs (pricing rule, accounts roles IC_RECEIVABLE/IC_PAYABLE, auto-mirror on/off, default warehouse).
* When company A posts an IC sales invoice to party = company B, the system creates B's vendor invoice (and the receipt if goods) **in the same UoW** (both companies are in the same tenant/DB), linked with
  `ic_link_id`; a failure on either side rolls back both (S10). IC payments/netting. An IC reconciliation report (balances must mirror).
* Permissions: the user posting in A must have the IC permission; B-side documents are created by a system principal acting with recorded causation; B's users see them per policy.

## Consolidation [A] (flag `consolidation`)
* Consolidation groups (parent, subsidiaries, ownership %, method: full/proportional/equity), chart mapping to a group CoA, currency translation (closing rate for balance sheet, average for P&L,
  historical for equity; CTA to OCI), eliminations (IC balances, IC revenue/COGS, unrealized profit in inventory per S10, investment vs equity), non-controlling interest, consolidated statements,
  and audit of elimination entries in a consolidation book (no postings in the subsidiaries' books).

## API
`/api/v1/treasury/bank-accounts`, `/cash-boxes`, `/transfers` (+`:post`), `/statements` (+`:import`), `/statement-lines` (+`:match`, `:unmatch`, `:create-entry`), `/reconciliation-rules`,
`/payment-batches` (+`:approve`, `:generate-file`, `:mark-sent`), `/exchange-rates:sync`, `/cash-position`; `/api/v1/intercompany/agreements`, `/reconciliation`; `/api/v1/consolidation/groups`,
`/runs` (+`:preview`, `:post`), `/reports/*`.

## Required tests (T-P16-nn)
01 Statement import + auto-match + fees. 02 Bank GL = statement balance after reconciliation (report). 03 Payment batch SoD and dual approval. 04 S10 intercompany atomic both sides (failure injection on B rolls back A).
05 IC reconciliation report mirrors. 06 TRM adapter with a fixture and manual override approval. 07 GMF posting and the exemption flag. 08 Consolidation translation and eliminations on a two-company fixture `[A]`.
09 Bank account number masked across channels.

## Acceptance (G2)
S10, S12 green; reconciliation UI usable by keyboard.

## Sources
BM-03 Business Central consolidation; BM-04 NetSuite Automated Intercompany Management (auto elimination entries at period close, IC reconciliation report); BM-04b NetSuite elimination subsidiaries (eliminations post only to a dedicated elimination entity, which is the model for our consolidation book); R-BK-01/02.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P16.md` when done)
```
# Handoff P16
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
