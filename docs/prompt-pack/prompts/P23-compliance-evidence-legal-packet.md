> **Prompt P23 · Wave W1 light, W4 full · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04, shared/05, shared/09 (all); shared/10 §C and §F. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P23 — Compliance Evidence, Regulatory Re-verification and Legal Review Packet

## Mission
Keep the regulatory claims honest and produce the packet that Colombian professionals need to sign off (EG-7, EG-8), plus the operator documents needed for the external gates.
You verify; you do not invent. You own the `docs/compliance/` tree and the regulatory test map.

## Owned paths
`docs/compliance/**` (except files assigned to P06/P15 inside it: `parameter-verification.md` (P06) and `annex-versions.md` (P15), which you review), `tests/regulatory/map.yaml`, `docs/STATUS.md`
compliance section (via P00).

## Start
Wave 1 in a light mode (the source re-verification log), full mode in Wave 4.

## Tasks
1. **Source re-verification:** for every entry in shared/05, re-open the URL at build time; record the HTTP status, the date, the title, and whether the cited content is still present (quote ≤ 25 words + a hash of the page
   section). For SECONDARY/SEARCH entries, try to find and open the official text (DIAN normograma, Secretaría del Senado, Función Pública, SUIN-Juriscol, Diario Oficial, ministries, Corte Constitucional, Consejo de
   Estado). Update `docs/compliance/source-register-verified.md` (a copy of shared/05 with new columns). Never upgrade a status without an opened official source.
2. **Change watch:** list the regulations likely to change soon (2027 UVT/SMMLV/transport in December 2026; Res. 227 modifications; Decreto 572 and SMMLV litigation; pension reform effective date; Ley 2466
   gradual dates) and create a P07 scheduled job template "regulatory review" monthly for operators, with a checklist.
3. **Regulatory test map** `tests/regulatory/map.yaml`: each R-* row in shared/04 → the test ids that cover it, or the reason it is not testable in software (for example a contract). P22's traceability generator includes it.
4. **Legal review packet** `docs/compliance/legal-review-packet/`:
   * `00-cover.md` (scope, versions, what the reviewer must sign, the list of DISPUTED/UNKNOWN items).
   * `tax.md` (tax codes, withholding tables, ICA approach, exógena formats, retention periods) with the parameter tables exported from P06 and their statuses.
   * `labor-payroll.md` (formula catalog from P13, parameter tables, boundary-date behavior, S6/S7 results, liquidation tables, the pension-reform flag rationale).
   * `einvoicing.md` (annex versions, document types, contingency handling, numbering, RADIAN scope, evidence retention).
   * `data-protection.md` (data inventory from the DSR registry, classifications, consent flows, biometric alternative, international transfer analysis for cloud and LLM providers per Art. 26, RNBD helper,
     incident runbook).
   * `accounting.md` (the CoA template, account roles, the posting matrix with scenario ledger dumps, NIIF policies per group, multi-book and tax differences) for the contador (EG-8).
   * A sign-off form per area (name, professional card/registration number, date, scope, exceptions).
5. **Operator gate guides** `docs/compliance/gates/EG-01..EG-13.md`: step-by-step instructions, the required inputs, where to configure them in the system, and how to verify them (for example the DIAN habilitación
   test-set procedure using the P15 runner).
6. **Data processing documents:** a privacy policy template (política de tratamiento) in Spanish, consent texts (employees, customers, biometrics), and a subprocessor list template; marked "template, requires legal review".
7. **Honest STATUS:** supply the compliance section of `docs/STATUS.md`: verified vs unverified counts, open blockers B-01..B-16 (+ new), external gates, and the exact wording of the claims allowed ("designed to
   support...", never "certified" or "compliant" until EG-7 is signed).

## Required checks
T-P23-01 Every R-* row has a test or a documented non-testable reason. 02 Every parameter in production fixtures links to a source with an access date. 03 The STATUS claims linter (no "certified/compliant/
production-ready" strings while EG items are open). 04 The re-verification log covers 100% of shared/05 entries.

## Acceptance (G4)
The packet complete, the gate guides complete, the source register re-verified, the STATUS honest.

## Sources
All of shared/05.


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P23.md` when done)
```
# Handoff P23
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
