> **Prompt P06 · Wave W1 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 (all); shared/05 (all); shared/09 B-01…B-16; shared/11 (demo tenant parameters). Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P06 — Tax Engine, Regulatory Parameters and Colombian Localization

## Mission
Own the **dated parameter store** and the **tax engine** (VAT, withholdings, ICA, consumption tax, SIMPLE), Colombian master data (NIT/DV, DIAN code lists, DANE municipalities,
holidays, tax calendar), and the verification workflow that keeps regulatory values honest (constitution C1-9, shared/04).

## Owned paths
`backend/tax/**`, `backend/localization_co/**`, `frontend/src/features/tax/**`, `tests/regulatory/**` (shared with P13 for payroll cases via CCR).

## Dependencies
G0. Implements `ParameterProvider` and `TaxService`. Publish fakes on day 1.

## Parameter store
* `Parameter`(code, scope: global/company/municipality, value (typed: decimal, percent, json table), valid_from, valid_to, status ∈ {VERIFIED-PRIMARY, VERIFIED-SECONDARY, UNKNOWN, DISPUTED},
  source_id (FK to `SourceRecord`: url, title, accessed_at, method, excerpt hash), legal_history (JSON events: suspended/revoked/annulled with dates and source), legal_reviewed_by/at, notes).
* Seed `tax/fixtures/parameters_2026.yaml` from shared/04 rows with their exact statuses: UVT 2026 52,374 (VERIFIED-PRIMARY S-08); SMMLV 1,750,905 (DISPUTED S-15/S-33); transport allowance
  249,095 (VERIFIED-SECONDARY); Decreto 572 withholding bases (DISPUTED); VAT 19%/5% and ReteIVA 15% default (VERIFIED-PRIMARY, ET 468/468-1/437-1); health/pension/FSP/CCF/SENA/ICBF/exoneration (VERIFIED-PRIMARY, R-LB-08);
  ARL table, FSP scale, ET 383 table cells (VERIFIED-SECONDARY until you open them: B-05). All still need the EG-7 stamp. **2027: no rows.**
* **Re-verification task (do this during your build, with web access if available):** open each official source in shared/05 marked SEARCH/SECONDARY, and if confirmed upgrade the
  status with the URL and access date. If not reachable, keep the status. Never upgrade without opening the source. Record the result in `docs/compliance/parameter-verification.md`.
* Admin UI: a parameter timeline per code, the legal history, a "load new official value" form requiring the source URL + document upload, two-person approval, and impact preview
  (which open payroll runs/invoices would change).
* Production rule (C1-9): `get()` raises `ParameterUnknown` for UNKNOWN and `ParameterNotVerified` when the value lacks a legal-review stamp. DISPUTED values with a stamp are allowed, but every calculation using them stores `parameter_snapshot` with the status, and the UI shows a banner.

## Tax engine
* `TaxCode`(company, type ∈ {IVA, INC (impuesto nacional al consumo), RETE_RENTA, RETE_IVA, RETE_ICA, AUTORRETENCION, ICA, other}, rate (parameter ref), base rule (net, VAT amount for ReteIVA,
  percentage of base), minimum base in UVT (parameter ref), applicability matrix: seller regime × buyer regime × item/service concept × municipality, accounts roles, DIAN code (annex code list)).
* `Party tax profile`: person type (natural/jurídica), regime (responsable de IVA / no responsable), gran contribuyente, autorretenedor, agente de retención IVA, régimen simple, ICA municipalities,
  economic activity CIIU, fiscal responsibilities codes (RUT responsabilidades, for example O-13, O-15, O-23, O-47, R-99-PN: **verify the current list in the annex code tables**).
* `TaxService.compute(document_lines, seller, buyer, date, municipality) -> TaxResult` (per line taxes, withholdings, totals, rounding per shared/07 §8, explanation trace listing every rule
  and parameter used: used by the UI "why this tax?" popover and by the AI agent).
* Withholding concepts (compras, servicios, honorarios, arrendamientos, etc.) as parameter tables with bases in UVT and rates, effective-dated (Decreto 572 history kept: pre-572 set and 572 set with dates).
* ICA: per municipality DANE code; tariff by CIIU activity in per-mil; ReteICA bases; **ships without municipal tariffs (B-13)**; an import template for tariffs.
* Calendar: tax due dates by obligation and NIT last digit (yearly data import) and reminders through the P07 scheduler.

## Localization data
* NIT DV algorithm (weights 3,7,13,17,19,23,29,37,41,43,47,53,59,67,71) with tests; document types (CC, CE, NIT, PP, TI, PEP, PPT, NIT extranjero) as annex codes.
* DANE departments/municipalities (DIVIPOLA) import; DIAN code lists from the pinned annex version (unit of measure, payment means, tax schemes, document types) loaded as versioned data by P15/P06 together.
* Holidays: Ley 51/1983 algorithm (Easter-based + Emiliani moves) with tests for 2026 and 2027 and an admin override.
* es-CO formats and number-to-words in Spanish for amounts (needed on invoices: "valor en letras").

## API
`/api/v1/tax/parameters` (+`:propose`, `:approve`), `/sources`, `/tax-codes`, `/withholding-concepts`, `/ica-tariffs`, `/party-tax-profiles`, `/compute` (stateless calc, also used by AI),
`/calendars`, `/localization/municipalities`, `/localization/holidays`, `/localization/nit:validate`.

## Required tests (T-P06-nn)
01 DV algorithm (known valid NITs). 02 UVT-based minimum bases at the exact boundary (base − 1, base, base + 1). 03 Date-effective switching of withholding sets on 2026-07-01 per R-TX-03 history.
04 UNKNOWN → raises; production + non-verified → raises; DISPUTED → computes and snapshots. 05 ReteIVA only when the buyer is an agent and the seller is VAT-responsible (per matrix fixtures). 06 Rounding per line vs document.
07 Explanation trace complete. 08 Holidays 2026/2027. 09 Parameter approval requires two people. 10 Impact preview lists the affected documents.

## Acceptance (G1)
Engine and store merged; fixtures with statuses; `docs/compliance/parameter-verification.md` produced; at least the S-08 UVT row re-opened and confirmed.

## Sources
S-08 (UVT), S-09/S-32 (withholding), S-10 (ET 114-1, 336, 383, 437-1, 468, 468-1, 632, 714), S-15/S-33 (SMMLV), S-36..S-39 (contribution rates), S-41 (holidays), S-02 (annex code tables).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P06.md` when done)
```
# Handoff P06
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
