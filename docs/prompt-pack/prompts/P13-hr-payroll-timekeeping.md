> **Prompt P13 · Wave W2 · Colombia AI-first ERP+CRM prompt pack v1.1.0 · research date 2026-11-08**
>
> **Starter context (applies to every agent; read before anything else).** You are one of several coding agents building, in parallel, a SaaS multi-tenant,
> multi-company ERP+CRM for Colombia. **Paths:** this pack lives at `docs/prompt-pack/` *relative to the repository root*. Every path in the pack is resolved from the
> repository root (never from the filesystem root): `shared/…` means `docs/prompt-pack/shared/…`, `prompts/…` means `docs/prompt-pack/prompts/…`, and code paths such as
> `backend/…`, `contracts/…`, `docs/…` are repo-root relative.
> **Reading (scoped, to save context):** always read `README.md` (sections "Honest scope statement" and "How to execute"), `shared/01-constitution.md` in full, and
> `shared/02-contracts-ownership-dag.md` §1, §3, §4. Then read **your required reading:** shared/04 §D, R-FE-09, R-DP-01; shared/07 §4; shared/09 B-01…B-05, B-12; shared/11 S6, S7, §Payroll. Open any other shared doc only when a task needs it; look things up by ID
> (R-xx, S-xx, ADR-xxx, B-xx, EG-xx, Sx). This prompt plus that reading is designed to be enough. **The constitution overrides anything in this prompt.**
> Work only in your owned paths. Anything else goes through a CCR file in `contracts/change-requests/`. When something is ambiguous, choose the safer, more conservative option,
> record the decision in your handoff, and keep going. Never hard-code a regulatory value, never invent a rate, code, layout, or date, and never call something done unless its tests pass.

# P13 — Human Resources, Payroll (Colombia) and Time Keeping

## Mission
Employee lifecycle, time and attendance, and Colombian payroll with dated legal parameters, **in-line GL posting of payroll runs and provisions**, PILA export, nómina electrónica hand-off, and
liquidation on termination. Requirements UR-12, UR-14. Every legal value comes from P06 parameters; nothing is hard-coded (C1-9).

## Owned paths
`backend/hr/**`, `backend/payroll/**`, `backend/timekeeping/**`, `frontend/src/features/{hr,payroll,timekeeping}/**`, payroll cases in `tests/regulatory/payroll/**` (CCR with P06).

## Dependencies
G1 (P05, P06, P07, P04); `FiscalGateway` (P15) for nómina electrónica.

## HR
`Employee` (personal data classified per Ley 1581; sensitive: health, biometrics; ID type/number; address; bank account (secret, encrypted); EPS, AFP, cesantías fund, CCF, ARL with risk class I–V;
dependents (for withholding deductions); emergency contacts), `Contract` (type: término indefinido, fijo, obra o labor, aprendizaje SENA, prestación de servicios is **not** payroll (flag and route to AP),
salary type ordinary/integral (≥ 13 SMMLV, parameter), part-time, start/end, position, cost center, workday schedule), `ContractHistory` (salary changes, promotions), `Position`, `OrgUnit`, documents,
onboarding/offboarding checklists, self-service portal (payslips, certificates (certificado laboral, certificado de ingresos y retenciones, formato 220: verify the current format), vacation requests, data updates).

## Time keeping
* Clock-in/out via web, kiosk (PIN/QR), and mobile with optional geofence; **biometric only with explicit consent and a non-biometric alternative** (R-DP-01; store templates only if unavoidable, encrypted, or
  better via device-side matching, keeping only a match result).
* Shift planning (templates, rotations), timesheets, overtime requests and approvals (P07), absence types.
* **Time classification engine:** split worked intervals into ordinary/overtime × day/night × weekday/Sunday-holiday using parameters effective on the **date of work**: night window (19:00–06:00 from
  2025-12-25; previously 21:00–06:00), Sunday/holiday surcharge (80% from 2025-07-01, 90% from 2026-07-01, 100% from 2027-07-01), night surcharge 35%, overtime premiums (25% day, 75% night),
  combined rules (night + rest-day additive: confirm in legal review), the rest day configurable per contract (Ley 2466 art. 14 par. 3; default Sunday), flexible 4–9 h days within the weekly average (art. 11), overtime ≤ 2 h/day and 12 h/week (art. 13), weekly maximum hours by date (Ley 2101 art. 3: 44 h until 2026-07-14, 42 h from 2026-07-15), monthly hour divisor parameter (B-12),
  habitual vs occasional Sunday work (3+ Sundays in a calendar month), compensatory rest. Overtime limits validation (parameters). Intervals crossing midnight and the boundary dates are split at rule changes.

## Payroll
* `PayrollPeriod` (monthly, semi-monthly; company calendar), `PayrollConcept` (earning/deduction/provision/employer contribution; salary-constitutive or not (Ley 1393 40% rule), affects IBC, affects
  provisions, affects withholding, accounting roles, DIAN nómina electrónica element mapping), `Novelty` (novedades: overtime, surcharges, commissions, bonuses, absences, incapacidades by type with pay rules,
  licencias, vacations taken, loans, embargos, voluntary deductions), `PayrollRun` (draft → calculated → approved → posted → paid; recalculation only in draft/calculated), `Payslip` lines with a calculation trace.
* **Calculation engine** (pure functions + parameters; deterministic; each line stores the formula id, inputs, and parameter snapshot with statuses):
  basic salary by days, transport allowance (≤ 2 SMMLV, not on vacation/incapacity days per rule params), overtime and surcharges from timekeeping, IBC computation (min 1 SMMLV; max 25 SMMLV; integral 70%;
  non-salary payments > 40% added), employee health 4% and pension 4%, FSP (≥ 4 SMMLV, scaled table), labor withholding (procedure 1 [P]; procedure 2 [A]) with deductions/exemptions per ET (UVT parameters),
  employer contributions (pension 12%, health 8.5% unless exonerated per ET 114-1, ARL by class, CCF 4%, SENA 2%, ICBF 3% unless exonerated), provisions (cesantías, intereses 12%, prima, vacaciones).
  **All rates are parameters.** Health, pension, FSP 1%, IBC cap, the 40% rule, CCF, SENA, ICBF, and the exoneration were verified from official text on 2026-11-08 (R-LB-08). The ARL table, the FSP scale above 16 SMMLV,
  interest on cesantías, prima, vacations, overtime premiums, and integral salary are still `VERIFIED-SECONDARY` (B-05): open the official text before upgrading. Everything still needs EG-7 legal review.
* Prima payments (June/December), cesantías consolidation and fund deposit file (before 15-Feb), interest payment (January), vacations (15 business days, holiday-aware) with the payment base rules.
* **Termination liquidation:** pending salary, prestaciones, vacations, indemnity by contract type and cause (parameterized tables; legal review), a liquidation document.
* **PILA export** (R-LB-09): file generation per the Res. 2388/2016 layout version (versioned generator; validate with operator test files at EG-4), novelties codes (ING, RET, IGE, LMA, VAC, etc.: verify).
* **Nómina electrónica** (R-FE-09): per employee per month NominaIndividual; adjustments NominaIndividualDeAjuste (replace/delete); the deadline is the first 10 days of the next month; P15 transmits; statuses shown.
* **Posting:** shared/07 §4, in-line when the run is posted; payment posting when paid (bank file via P16).
* Pension reform Ley 2381/2024: behind flag `pension_reform_2381` (default OFF; R-LB-10). Per C-264/2026 the exequible provisions take effect **2027-04-01** (no earlier enable date allowed); the returned articles are separate sub-flags. Routing rules are parameters; tests cover both flag states.
* Reports: payroll summary by concept/cost center, provisions, contributions by entity, cost per employee, headcount, certificates.

## API
`/api/v1/hr/employees` (+`:bulk`), `/contracts` (+`:amend`, `:terminate`), `/positions`, `/org-units`, `/self-service/*`; `/api/v1/timekeeping/clock-events` (+`:clock-in`, `:clock-out`), `/shifts`,
`/timesheets` (+`:approve`), `/overtime-requests` (+`:approve`), `/absences`; `/api/v1/payroll/periods`, `/concepts`, `/novelties` (+`:bulk`), `/runs` (+`:calculate`, `:approve`, `:post`,
`:simulate`, `:pay`), `/payslips`, `/liquidations` (+`:calculate`, `:post`), `/pila-files` (+`:generate`), `/electronic-payroll` (+`:generate`, `:adjust`), `/reports/*`.

## Field policy
Salary, bank account, health data, biometrics, dependents = restricted/sensitive; visible only to payroll roles and the employee themself (self-service). Managers see team schedules and absences, not salaries
unless granted. AI aggregates over salaries respect a k-anonymity threshold (min group size 5) for non-payroll roles.

## Required tests (T-P13-nn)
01 S6 exact values (fixture rates). 02 S7 boundary cases T7-1..T7-7. 03 Transport allowance threshold at exactly 2 SMMLV and +1 peso. 04 IBC caps and the 40% rule. 05 Exoneration on/off. 06 FSP at the 4 SMMLV boundary.
07 Withholding procedure 1 vs UVT table. 08 Vacations business days with holidays. 09 Prima semester proration. 10 Liquidation cases (fixture). 11 Provisions = GL (invariant 6). 12 Payroll run posting atomic.
13 Nómina electrónica XML validates (fixture XSD) and the CUNE golden vector from P15. 14 PILA file layout validates against the fixture spec. 15 2027 dates fail closed while parameters are UNKNOWN. 16 Field
masking across channels (salary hidden to a manager in API/export/AI/MCP). 17 Biometric clock disabled without consent; an alternative is available. 18 Pension reform flag both states.

## Acceptance (G2)
S6/S7 green; `docs/modules/payroll.md` with the formula catalog and the parameter mapping; legal golden cases left as a registered blocker until EG-7 (shared/11 §Payroll).

## Sources
S-05 (Res 13/2021), S-10 (ET 114-1, 383, 387, 206), S-15/S-33 (SMMLV, transport), S-34 (Ley 2466 arts. 10, 11, 13, 14), S-35 (Ley 2101 art. 3), S-36 (Ley 100 arts. 18, 20, 204), S-37 (Ley 1393 art. 30), S-38/S-39 (Ley 21/1982, Ley 89/1988),
S-40 (Ley 50/1990 art. 99), S-41 (Ley 51/1983), S-18 (CST, not opened), S-21 (PILA), S-22/S-46 (C-264/2026, Ley 2381), S-24 (Ley 1581).


---
## Universal rules for this prompt (from the constitution, repeated so this file is self-contained)
* Money is `Decimal` with the NUMERIC precisions in C2 (amount 20,4; quantity 20,6; unit cost 24,8; FX rate 24,10; percent 9,6). Never use `float`. Every write goes through `kernel.uow.unit_of_work()`. No external I/O inside a transaction (use the outbox).
* Every read and write goes through `iam.policy` (row and field). This holds for API, export, report, AI, MCP, and webhooks.
* Every state change is an API action with an `Idempotency-Key`. The UI uses only the public API.
* Regulatory values are read through the `kernel.parameters` interface (P01) backed by the `tax.parameters` store (P06), with status and source. UNKNOWN fails closed; in production a value without a legal-review stamp blocks; DISPUTED computes with a warning snapshot (C1-9).
* Forbidden: `TODO`, `FIXME`, `assert True`, skipped tests without a registered `BLK-` blocker, fake endpoints, bare `except`, and `print` in backend code.
* Tests: unit + integration with real PostgreSQL; property-based tests for calculations; failure-injection tests for every posting path; leak tests for every new read path.

## Handoff (write `docs/handoffs/P13.md` when done)
```
# Handoff P13
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
