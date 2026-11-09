> **Prompt P21 · Wave W3 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 R-AC-01, R-AC-02, R-DP-03, R-DP-05; shared/11 S11. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P21 — AI Onboarding Agent and Tenant Setup

## Mission
Get a new tenant from sign-up to issuing its first compliant (sandbox) invoice and running operations **as fast as possible**, including migration of **existing data** (spreadsheets, exports from
other systems: Siigo, World Office, Alegra, Helisa, SAP B1, Odoo, QuickBooks exports as CSV/XLSX), using a conversational agent that configures the system through the public API. Requirement UR-18.

## Owned paths
`backend/onboarding/**`, `frontend/src/features/onboarding/**`, `backend/kernel/fixtures/demo_tenant.yaml` (data for `seed_demo`, by CCR with P01), `backend/onboarding/templates/**` (CoA PUC template, role
templates, document templates defaults).

## Dependencies
G2 + P18 (import), P19 (agent runtime and provider gateway: onboarding is a specialized agent profile with its own tool subset), P05, P06, P04, P15.

## Flow (resumable checklist with progress; each step has a manual UI equivalent)
1. **Company profile:** NIT + DV validation; razón social; NIIF group (a questionnaire implementing the DUR 2420 criteria verified in R-AC-01: issuer or public interest; >200 workers or >30,000 SMMLV assets plus the four foreign/IFRS/50%-trade conditions; the Group 3 six-condition test; then "confirm with your contador"); tax responsibilities (from a RUT PDF upload: the AI extracts
   fields, the user confirms each one); municipality; economic activities (CIIU); fiscal year; currency; branches; logo.
2. **Chart of accounts:** choose the PUC-based template (comercial/servicios), import their existing CoA (mapping assistant: AI proposes mappings to account roles; the user confirms), or start minimal.
   Account determination defaults generated from the roles. Mark as "pending contador review" (EG-8).
3. **Taxes:** propose tax codes and withholding concepts from the company profile; ICA tariffs for their municipalities (the user uploads/types; the agent never invents municipal rates); a parameter verification status shown.
4. **Users and roles:** invite users, assign role templates, and explain field policies (cost/salary visibility) in plain language, with the policy simulator.
5. **Masters import:** customers, suppliers, items, price lists, warehouses, employees, assets via P18 with the AI column mapping, data cleaning suggestions (DV errors, duplicates, municipality names → DANE codes,
   UoM normalization), and a dry-run preview.
6. **Opening balances:** cut-off date; trial balance import → opening entry; open AR/AP items (detail must equal the control account balances, otherwise the agent shows the differences); opening stock with costs (must
   equal the inventory account); fixed assets with accumulated depreciation per book; payroll accumulations for the current year (for withholding and prestaciones continuity); bank balances. Reconciliation report;
   the user confirms; post.
7. **Electronic invoicing setup:** choose the provider mode (PT adapter / direct `[A]`), sandbox credentials, numbering ranges (upload the resolution or sync), the test-set runner (habilitación) with
   step-by-step DIAN portal instructions (the agent explains, the user performs portal steps; EG-1), templates for the representación gráfica.
8. **Payroll setup** (optional): entities (EPS/AFP/ARL/CCF), concepts, the pay calendar, and the first period with the novelties import.
9. **Go-live checks:** the trial balance balanced; sub-ledgers reconcile; test invoice in sandbox accepted; users with MFA; data protection: a privacy policy template + a consent text configured (R-DP-05); the RNBD
   applicability check (assets > 100,000 UVT: R-DP-03) with guidance.
10. **First-week coach:** contextual tips and daily checklist suggestions based on usage.

## Agent design
* Tool subset from the P19 catalog plus onboarding-specific tools (`company_profile_set`, `rut_extract`, `coa_template_apply`, `import_*`, `opening_balance_prepare/commit`, `einvoice_setup_*`).
* Every write goes through plan cards with confirmation (P19 rules); bulk operations show the dry-run report.
* Document understanding: RUT PDF, numbering resolution PDF, trial balance XLSX: extraction via the provider's vision/structured output or a local parser; the user confirms each extracted field; the confidence is shown.
* Never fabricates regulatory values: where data is missing it asks the user or leaves the parameter UNKNOWN (fail closed) with an explanation.
* Spanish-first conversational UX, with a progress sidebar, "skip for now", and resume later.

## Demo tenant
Fill `demo_tenant.yaml` for the scenarios in shared/11 (companies A and B, users, items, parties, parameters) so `make seed-demo` works.

## API
`/api/v1/onboarding/sessions` (+ steps, `:resume`), `/checklist`, `/templates` (CoA, roles, documents), `/rut:extract`, `/opening-balances` (+`:prepare`, `:validate`, `:commit`), `/go-live-check`.

## Required tests (T-P21-nn)
01 S11 end-to-end with a scripted LLM (cassette) and with a manual-UI path. 02 Invalid DV detection + fixes. 03 Opening balances must reconcile, otherwise commit blocked. 04 The agent never writes without confirmation.
05 Missing ICA tariff → UNKNOWN, not invented. 06 Resume after browser close. 07 Time-to-first-invoice measured (< 60 min of guided steps with the fixture pack, measured by the scripted run). 08 RUT extraction fields require confirmation.
09 `seed_demo` loads and the scenarios pass.

## Acceptance (G3)
S11 green; onboarding usable without AI (manual path) and with AI.

## Sources
R-AC-01/02 (S-42, S-14), R-DP-03/05 (S-25, S-26), R-FE-01 (S-01), shared/06 (onboarding UX).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P21.md` when done)
```
# Handoff P21
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
