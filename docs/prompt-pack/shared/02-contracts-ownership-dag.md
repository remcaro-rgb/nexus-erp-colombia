# Contracts, Path Ownership, Dependency DAG and Waves

Owner: P00/P02. Read-only for workers.

## 1. Repository layout and owners

```
/                                    P01 (root files), P03 (CI files)
├── pyproject.toml, uv.lock          P01  (dependency CCRs → P01)
├── package.json (workspace root)    P08  (frontend deps CCRs → P08)
├── docker-compose.yml / .dev / .prod P03
├── deploy/                          P03  (Dockerfiles, compose overlays, backup/restore scripts, runbooks)
├── .github/ or .gitlab-ci.yml       P03
├── contracts/                       P02  (frozen at G0)
│   ├── openapi/root.yaml            P02  (shared components: Money, Error, Page, Audit, Ids)
│   ├── openapi/modules/<module>.yaml   module owner after G0 (only paths/schemas prefixed by module)
│   ├── events/<module>/*.json       module owner after G0; envelope schema P02
│   ├── permissions/catalog.yaml     P02 seeds; each module appends only its own `<module>.*` codes via CCR-free append
│   └── change-requests/             any agent may add files; only P00/P02 resolve
├── backend/
│   ├── erp/ (settings, urls, asgi, celery)   P01
│   ├── kernel/   P01   money, ids, uow, outbox, idempotency, sequences, errors, tenancy context, base models, clock
│   ├── iam/      P04   tenants, companies, users, roles, ABAC policies, field policies, RLS SQL, delegated identities, break-glass
│   ├── ledger/   P05   CoA, journals, periods, posting engine, FX, dimensions, tax-difference register
│   ├── tax/      P06   tax codes, parameters, withholding, ICA, calendars, exogenous info, certificates, CO localization data
│   ├── workflow/ P07   approvals, SoD, audit log (hash chain), scheduler, notifications, attachments
│   ├── inventory/ P09
│   ├── sales/ crm/ P10   (incl. `sales/party/`: the shared Party master exposed via PartyService)
│   ├── purchasing/ P11
│   ├── pos/      P12
│   ├── hr/ payroll/ timekeeping/ P13
│   ├── assets/   P14
│   ├── einvoice/ P15   UBL builders, signing, CUFE/CUDE/CUNE, provider adapters, contingency, RADIAN [A]
│   ├── treasury/ consolidation/ P16
│   ├── reporting/ documents/ P17   semantic layer, report builder, doc templates
│   ├── dataio/   P18   import/export engine
│   ├── ai_agent/ P19
│   └── onboarding/ P21
├── mcp_server/   P20  (separate Python service, calls the public API only)
├── specs/fiscal-hash/  P15  (CUFE/CUDE/CUNE spec + JSON test vectors; server-side Python is the [P] implementation)
├── frontend/
│   ├── src/app, src/shell, src/design-system, src/lib   P08
│   └── src/features/<module>/   module owner (P09..P21 own their feature folder)
├── tests/
│   ├── contract/   P02 (schema tests), P22 (runs)
│   ├── e2e/        P22
│   ├── cross_module/ P22  (financial consistency suite; module agents contribute scenarios by CCR)
│   ├── security/   P22
│   ├── perf/       P22
│   └── regulatory/ P06 (tax), P13 (payroll/), P23 (map.yaml)
└── docs/
    ├── adr/        P00 (copies shared/03 at start; new ADRs by CCR)
    ├── handoffs/   each agent writes its own file
    ├── compliance/ P23
    └── runbooks/   P03
```
Other notable owned files: `backend/ai_agent/tools/catalog.yaml` (P19; P20 consumes it read-only at build time), `backend/reporting/semantic/` (P17; modules contribute YAML by CCR),
`backend/kernel/fixtures/demo_tenant.yaml` (P21 data, P01 loader), `docs/compliance/` (P23; `parameter-verification.md` P06, `annex-versions.md` P15).
Module test paths: `backend/<app>/tests/` (module owner). Frontend tests: `frontend/src/features/<module>/__tests__/`.

## 2. Contract freeze (G0) contents (P02 delivers)
* OpenAPI 3.1 root and module fragments with every endpoint listed in each module prompt (request/response schemas, errors, idempotency header,
  pagination, filtering grammar, `fields=` sparse fieldsets, ETag/If-Match for updates).
* Event envelope: `{event_id: uuidv7, event_type: "<module>.<entity>.<verb>.v<N>", occurred_at, tenant_id, company_id, actor: {type, id, on_behalf_of},
  correlation_id, causation_id, payload, schema_version}`.
* Shared value types: `Money {amount: string(decimal), currency: ISO4217}`, `Quantity {value: string(decimal), uom}`, `Rate`, `Percent`, `DateRange`,
  `PartyRef`, `DocumentRef {type, id, number}`, `AuditStamp`, `Problem` (RFC 9457 application/problem+json with `code`, `trace_id`).
* Permission catalog grammar: `<module>.<resource>.<action>` (actions: read, create, update, delete, approve, post, void, export, admin) and
  field classification tags: `public`, `internal`, `confidential`, `restricted-pii`, `sensitive-pii` (Ley 1581 sensitive data incl. biometrics, health), `secret`.
* Error code registry `contracts/errors.yaml`.
After G0, breaking changes need a CCR approved by P00 plus a version bump. Additive changes in a module's own fragment are allowed without a CCR,
but CI contract tests must stay green.

## 3. Dependency DAG

```
W0: P01 ─┬─ P02 ─┬─ P03
         │       │
G0 ──────┴───────┴──────────────────────────────────────────────
W1: P04(IAM) P05(Ledger) P06(Tax) P07(Workflow/Audit/Sched) P08(UX shell)
     (P05 uses P04 interfaces via kernel stubs; P06 independent; P07 uses P04)
G1 ─────────────────────────────────────────────────────────────
W2: P09 Inventory ← P05,P06
    P10 Sales/AR/CRM ← P05,P06,P09 (stock via inventory service interface frozen at G1)
    P11 Purchasing/AP ← P05,P06,P09
    P12 POS ← P10 services (sale posting), P09, P15 interface
    P13 HR/Payroll/Time ← P05,P06,P07
    P14 Fixed Assets ← P05,P11 (capitalization interface)
    P15 DIAN gateway ← P06 (codes), P07 (scheduler); called by P10,P11,P12,P13 via frozen interface
    P16 Treasury/IC/Consolidation ← P05,P10,P11
G2 ─────────────────────────────────────────────────────────────
W3: P17 Reporting/Semantic ← all W2 read models
    P18 Import/Export ← all W2 service APIs
    P19 AI Agent ← P17, P04, P07
    P20 MCP ← public API + P04 OAuth
    P21 Onboarding ← P18, P19, P06, P05
G3 ─────────────────────────────────────────────────────────────
W4: P22 QA/Sec/E2E (continuous from W1)   P23 Compliance packet
G4 (Release candidate) → External gates EG-1..EG-13 → Production
```
Wave 2 agents run in parallel because they code against **interfaces frozen at G1** (Python protocols in `kernel/interfaces/*.py`, owned by P01,
written during W0 from the P02 contracts). For example, P10 calls `inventory.api.StockService.issue(...)`. P09 implements it. Until P09 merges, P10's tests
use the **contract-verified fake** that P09 publishes on day 1 of W2 at `backend/inventory/testing/fakes.py`. Cross-module tests in `tests/cross_module`
must run against the real implementations before G2.

## 4. Merge and migration protocol
1. Each agent works on branch `wave<N>/<prompt-id>-<slug>` (or a worktree). Rebase on `main` at least daily.
2. Merge queue (P03 config): one merge at a time; the full test suite plus `makemigrations --check` plus contract tests must pass on the rebased head.
3. Migration numbering conflicts within an app are resolved by the owning agent only. Cross-app migration dependencies must point to merged migrations.
4. Shared manifests: only the owner edits them. Dependency requests use `contracts/change-requests/DEP-<agent>-<n>.md` (package, version, license, reason).
5. Feature flags: `kernel.flags` (P01). Every `[A]` item ships behind a flag, default off.

## 5. Gates (internal)
| Gate | Exit criteria (all evidence in `docs/gates/G<N>.md`, written by P00) |
|---|---|
| G0 | Contracts v1.0.0 tagged; kernel (money, ids, uow, outbox, idempotency, sequences, tenancy context) merged with ≥95% branch coverage; compose stack up; CI pipeline green including the forbidden-pattern scan |
| G1 | IAM: RLS on every tenant table, field policy single path, leak tests green. Ledger: posting engine with invariants + property tests. Tax: parameter store + VAT/withholding calc. Workflow: approvals/audit hash chain/scheduler. UX shell: a11y CI green. Interfaces in `kernel/interfaces` frozen v1 |
| G2 | All W2 modules merged; `tests/cross_module` suite green (seed scenarios S1–S12 in shared/11); trial balance = sub-ledgers (AR, AP, inventory, fixed assets, payroll liabilities) at each scenario checkpoint; concurrency/negative tests green |
| G3 | Reporting/AI/MCP/import/onboarding merged; leak tests across API/AI/MCP/export/report/search green; prompt-injection suite green; import resumability and atomicity tests green |
| G4 | Full E2E + a11y + security scans + backup/restore drill + DIAN sandbox fixtures green; acceptance checklist (shared/10) complete except external gates; legal-review packet produced |
