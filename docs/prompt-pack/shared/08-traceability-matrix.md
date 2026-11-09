# Traceability Matrix: user requirement → prompts → tests → gates

Test IDs are defined inside each prompt's "Required tests" section (`T-Pxx-nn`). Cross-module scenario IDs (`S1..S12`) are in shared/11.
P22 builds `docs/traceability.csv` automatically from test markers (`@pytest.mark.req("UR-xx")`). G4 fails if any UR has no passing test.

| UR | User requirement (verbatim intent) | Primary prompts | Supporting | Key tests / scenarios | Gate |
|---|---|---|---|---|---|
| UR-01 | SaaS, multi-tenant, **multi-company** | P01, P04 | P16, P08 | T-P04-01..06 (RLS/tenant), T-P16-04 (IC), S10 | G1, G2 |
| UR-02 | **AI-first** | P19, P21 | P08 (command palette), P17 | T-P19-*, T-P21-* | G3 |
| UR-03 | ERP + **CRM** | P10 | P17, P19 | T-P10-10..14 | G2 |
| UR-04 | Complies with **Colombian regulation** | P06, P15, P13 | P11, P12, P05, P23 | regulatory matrix rows R-* each mapped to ≥1 test in `tests/regulatory/` (P23 maintains the map), S1–S9 | G4 + EG-1..EG-8 |
| UR-05 | Research local regulation constraints and guidelines | shared/04, shared/05 | P23 | P23 verifies each source at build time (re-open log) | G4 |
| UR-06 | ERP best practices, security, features; top ERP practices | shared/06, shared/03 | all | P22 ASVS L2 checklist, API Top 10 tests | G4 |
| UR-07 | Module 1: **Accounting and finance** | P05, P16 | P06, P17 | T-P05-*, T-P16-*, invariants 1–6, 8 | G1, G2 |
| UR-08 | Module 2: **Inventory and costs** | P09 | P11, P10, P12 | T-P09-* (FIFO/WAC/landed cost/concurrency), S2, S3, S5 | G2 |
| UR-09 | Module 3: **Invoicing** (incl. DIAN e-invoicing) | P10, P15 | P06, P12 | T-P10-01..09, T-P15-*, S1, S4 | G2, EG-1 |
| UR-10 | Module 4: **Purchases** | P11 | P09, P15 | T-P11-*, S2, S3 | G2 |
| UR-11 | Module 5: **POS** | P12 | P09, P10, P15 | T-P12-* (online atomic checkout, disconnected-checkout blocked, reconnect commit, DIAN contingency, cash session), S4, C1-12 | G2 |
| UR-12 | Module 6: **HR, payroll, time keeping** | P13 | P06, P15, P05 | T-P13-* (boundary dates Ley 2466/2101), S6, S7 | G2, EG-4, EG-7 |
| UR-13 | Module 7: **Fixed assets (depreciation)** | P14 | P05, P11 | T-P14-*, S8 | G2 |
| UR-14 | **All money processes post in-line to the ledger** | P05 | P10–P16 | invariant 2; T-P05-07 (atomic rollback), T-P22-FIN-01 (every money endpoint creates an entry in the same tx: rollback injection) | G2 |
| UR-15 | **All stock processes update inventory in-line** | P09 | P10–P12 | invariant 3, 7; T-P09-08 (failure injection after the stock write rolls back everything) | G2 |
| UR-16 | **Bulk import/export** | P18 | all module owners publish import specs | T-P18-* (1M rows, resumable, dry-run, atomic batches, field policy on export) | G3 |
| UR-17 | **Model-agnostic AI agent**: query data, perform processes, schedule tasks, custom reports/documents, simulations/what-if | P19 | P17, P07, P04 | T-P19-01..20 incl. provider swap test, the approval-before-write test, the simulation-never-posts test (C1-3) | G3 |
| UR-18 | **Onboarding AI agent** incl. existing data | P21 | P18, P19, P05, P06 | T-P21-* ; S11 (migration from spreadsheet + opening balances) | G3 |
| UR-19 | Tech stack with the best maintenance cost-benefit, **container deploy** | P01, P03 | shared/03 ADR-001/002/014 | T-P03-* (compose up, health, backup/restore drill) | G0, G4 |
| UR-20 | **Best UI/UX practices** | P08 | all `features/*` | T-P08-* (axe a11y, keyboard nav, Lighthouse budgets), usability heuristics review | G1, G4 |
| UR-21 | Granular data access: **row and field level** | P04 | all read channels | T-P04-07..20, invariant 12 across API/AI/MCP/export/report/search | G1, G3 |
| UR-22 | **All processes available via API** | P02 | all | T-P22-API-01 (every UI action maps to an OpenAPI operation; UI uses only the public API), schemathesis fuzzing | G3 |
| UR-23 | **MCP** so agents can connect | P20 | P04 (OAuth), P02 | T-P20-* (conformance, auth, scope, annotation, leak) | G3, EG-10 |
| UR-24 | Prompts for the **highest parallelism** | P00, shared/02 | — | DAG in shared/02; W2 runs 8 agents in parallel | — |
| UR-25 | Done in **one shot** with the prompts | P00 | P22, P23 | G0–G4 automated; external gates documented honestly (shared/09) | G4 |
