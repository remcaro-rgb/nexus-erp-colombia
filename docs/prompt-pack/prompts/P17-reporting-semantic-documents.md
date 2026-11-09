> **Prompt P17 · Wave W3 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/03 ADR-009; shared/04 R-TX-05…R-TX-07, R-AC-01; shared/07 §9. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P17 — Reporting, Semantic Layer, Financial Statements, Document Generation

## Mission
A **policy-aware semantic layer** (ADR-009) that powers standard reports, the custom report builder, dashboards, the AI agent's data questions, and document generation (invoices, certificates,
letters, contracts), plus the Colombian statutory report set.

## Owned paths
`backend/reporting/**`, `backend/documents/**`, `frontend/src/features/{reporting,documents}/**`.

## Dependencies
G2 (all module models and read services). Implements `ReportQueryService` and `DocumentRenderer`.

## Semantic layer
* A model registry where each module contributes `semantic/<module>.yaml` **by CCR into `backend/reporting/semantic/`** (P17 merges): entities, fields (type, classification from contracts),
  measures (sum/avg/count/distinct, custom Decimal-safe expressions), dimensions (time with fiscal calendar, company, branch, cost center, party, item, employee), joins (declared, no ad-hoc),
  default filters, row-policy hooks (`iam.policy.scope_queryset` per entity), and field masks.
* The query API: `{entity, measures[], dimensions[], filters[], sort[], limit, time_grain, compare_to}` → compiled to parameterized SQL by `reporting/compiler` (the only module allowed raw SQL besides iam/rls),
  executed with the session's RLS context on the replica if configured; masked fields cannot be selected, filtered, or grouped; small-group suppression (k ≥ 5) for sensitive aggregates (salaries).
* Query cost guard: statement timeout, row limits, and an EXPLAIN-based estimate rejecting heavy queries for interactive use (offer async export).
* Caching per (policy_version, query hash).

## Standard reports [P]
Trial balance, general ledger, journal book, libro mayor y balances, auxiliary by party (tercero), balance sheet (estado de situación financiera), P&L (estado de resultados) by function/nature,
cash flow (indirect, with mapping), changes in equity, AR/AP aging, sales analysis, purchases analysis, inventory valuation and kardex (from P09), payroll summaries (from P13), the fixed-asset movement,
tax reports (VAT summary by rate for the bimonthly/four-monthly declaration support, withholding summary for formulario 350 support, ICA by municipality), withholding certificates (renta, IVA, ICA) per party,
**información exógena** formats generated from the current format specifications (R-TX-05: specs loaded as versioned data by P06; generator by P17), budget vs actual, and comparative periods.

## Custom report builder [P]
A drag-and-drop UI over the semantic layer: pivot, charts (accessible), conditional formatting, saved reports with sharing to roles, scheduling via P07 (delivery by email/in-app as XLSX/PDF/CSV),
parameters (date range, company), drill-down to documents. AI can create and save reports through the same API (P19).

## Documents [P]
* Template engine: HTML/Jinja2-sandboxed templates → PDF (WeasyPrint or headless Chromium in a worker container), DOCX `[A]` (python-docx templates), XLSX (openpyxl).
* Template types: invoice/credit note representación gráfica (CUFE, QR, resolution text, valor en letras), POS ticket (80 mm), quotes, POs, payslips, certificates (laboral, ingresos y retenciones,
  withholding certificates), collection letters, asset QR labels, the Z report.
* A template editor with a preview against sample data, versioning, and per-company branding. Templates only access a whitelisted context (no arbitrary attribute access; sandboxed Jinja).
* Generated documents are stored as attachments with a hash and linked to the source record.

## API
`/api/v1/reporting/semantic-model` (introspection filtered by permission), `/query` (+`:explain`), `/reports` (saved, CRUD, `:run`, `:schedule`), `/standard/<report>`, `/exogena/<format>:generate`,
`/certificates:generate`; `/api/v1/documents/templates` (+`:preview`), `/render` (sync for small docs, 202 for batch).

## Required tests (T-P17-nn)
01 Semantic queries respect row policy (salesperson sees only own customers' sales). 02 Masked fields rejected in select/filter/group. 03 k-anonymity suppression. 04 Statements reconcile: BS assets = liabilities + equity;
P&L net = BS current-year result; cash flow ends at the bank GL balance (S1–S12 dataset). 05 Exógena generator against the spec fixture. 06 PDF representation contains CUFE and QR (parsed in the test). 07 Template sandbox
blocks attribute escapes (SSTI tests). 08 Query cost guard. 09 Scheduled report respects the owner's current permissions. 10 Cache invalidated by policy_version.

## Acceptance (G3)
All tests green; standard report set available in the UI; at least 10 report templates and 8 document templates shipped with the demo tenant.

## Sources
R-TX-05/06 (S-02, S-11), R-AC-01 (S-42), OWASP LLM Top 10 (for AI consumption of reports: T-12).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P17.md` when done)
```
# Handoff P17
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
