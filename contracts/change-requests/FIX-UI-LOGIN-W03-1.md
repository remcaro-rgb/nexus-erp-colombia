# CCR FIX-UI-LOGIN-W03-1 — make the registry navigation codes and their catalog aliases part of the permission contract

* **From:** FIX-UI-LOGIN-W03 (UI preview sign-in fix, after wave 02) · **To:** P02 (contracts: catalog grammar, OpenAPI), P04 (IAM policy)
* **Status:** OPEN · **Blocking:** no (the resolver ships in `backend/iam/policy/nav.py`; this CCR asks to freeze it in the contract)

## Context
`docs/spec/ui/module-registry.json` gates every module, submenu, tab, quick action, KPI and panel with a **navigation
code** (`shell.access`, `ledger.read`, `hr.access`, `inventory.cost.read`, …; 79 distinct strings today). The permission
catalog grammar is `<module>.<resource>.<action>` and none of the two-part codes (and several three-part ones) exist in
`contracts/permissions/catalog.yaml`, so the server could not filter `/api/v1/ui/modules` and the client's `can()` hid
every module, Inicio included (UL-4).

FIX-UI-LOGIN-W03 resolves each registry code server-side against the principal's **effective catalog permissions**
(`PolicyEngine.effective(ctx)` over `catalog_codes()`), deny by default:

1. `shell.access` → any authenticated INTERNAL principal (or AI/MCP acting for one) with an active company.
2. An explicit row of `NAV_ALIASES` (table below) → allowed iff an allowed catalog code matches one of its fnmatch patterns.
3. `<m>.<a>` with a catalog action (`read, create, update, delete, approve, post, void, export, admin`) → some allowed `<m>.<resource>.<a>`.
4. `<m>.<r>.<a>` with a catalog action → that code when the catalog has it, else any allowed `<m>.<r>….<a>` (resource prefix).
5. Anything else → denied.

`/api/v1/iam/me` and `/api/v1/ui/context` return `permissions = effective catalog codes ∪ resolved navigation codes`.

## Requested change
* **P02:** document navigation codes as a second, UI-only code family in `contracts/permissions/catalog.yaml`
  (e.g. a `navigation:` section listing every registry code with its rule or alias patterns), validated by the contract
  tests: every `permission` string of `module-registry.json` must be a catalog code or a listed navigation code.
  Add `GET /api/v1/ui/modules` and the extended `GET /api/v1/ui/context` (`companies[] {id, legal_name, nit, code}`,
  `roles[]`, `permissions[]`, `user {display_name, email}`, `session_idle_minutes`, `feature_flags`) to the OpenAPI
  bundle (continues CCR P08-1).
* **P04:** own `NAV_ALIASES` (`backend/iam/policy/nav.py`) as the implementation of that section; changes go through a CCR.
* **KPI codes:** `<m>.kpi.read`, `payroll.amounts.read`, `receivables.kpi.read`, `payables.kpi.read` have no catalog code
  yet, so those KPI cards/panels are absent for everyone. The provider tasks (P05/P09/P10/P11/P12/P13/P16) should append
  `<m>.kpi_*.read` (or the alias targets below) with their KPI endpoints; no edit of the resolver is needed.
* `integrations.read` targets `kernel.integration*.read`, which does not exist yet (deny until the integrations task adds it).

## Current `NAV_ALIASES`
| Navigation code | Catalog patterns | Reason |
|---|---|---|
| `admin.access`, `iam.admin` | `iam.*.admin`, `iam.user.update`, `iam.role.update`, `iam.role_assignment.create` | managing users/roles; `iam.break_glass.admin` alone would hide Administración from `administrador` (it denies break-glass) |
| `iam.external.admin` | `iam.external_account.{create,update,void}` | portal users are external accounts |
| `iam.audit` | `iam.event.read` | "Accesos denegados" reads the IAM security events |
| `tenant.admin` | `iam.company.{create,update}` | companies are IAM rows |
| `mcp.admin` | `iam.delegated_grant.{create,update}` | MCP clients act through delegated grants |
| `integrations.read` | `kernel.integration*.read` | no catalog module yet (deny) |
| `audit.read` | `*.audit.read`, `*.audit_*.read` | any module's audit resource (today `workflow.audit_event.read`) |
| `scheduler.read` | `workflow.scheduled_job.read` | scheduled jobs live in workflow (P07) |
| `party.read` | `sales.party.read` | the party master |
| `receivables.read` | `sales.ar_*.read` | AR subledger |
| `receivables.kpi.read` | `sales.ar_kpi*.read` | no code yet (deny) |
| `payables.read` | `purchasing.ap_*.read`, `purchasing.vendor_invoice.read` | AP subledger |
| `payables.kpi.read` | `purchasing.ap_kpi*.read` | no code yet (deny) |
| `hr.access` | `hr.*.read`, `timekeeping.*.read`, `payroll.*.read` | non-HR roles get no Talento |
| `payroll.amounts.read` | `payroll.amount*.read`, `payroll.payroll_report.read` | payroll amounts are sensitive |
| `inventory.cost.read` | `inventory.cost*.read`, `inventory.valuation.read` | cost = valuation layers |
| `inventory.move` | `inventory.move.read`, `inventory.transfer.read` | "Movimientos" lists moves and transfers |
| `ledger.close` | `ledger.period.{approve,update}`, `ledger.year_end_close.update` | closing periods |
| `treasury.reconcile` | `treasury.reconciliation*.{create,update}` | reconciliation work |
| `treasury.pay` | `treasury.payment_batch.{create,post}` | "Nuevo pago" |
| `assets.depreciate` | `assets.depreciation_run.{create,post}` | depreciation runs |
| `pos.sell` | `pos.ticket.create` | selling |
| `pos.supervise` | `pos.session.update`, `pos.return.post` | supervisors |
| `purchasing.receive` | `purchasing.receipt.create` | goods receipts |
| `purchasing.requisition.consolidate` | `purchasing.requisition.update` | consolidation changes requisitions |
| `purchasing.requisition.source` | `purchasing.rfq.create` | sourcing opens RFQs |
| `reporting.financial` | `reporting.standard_report.read` | statements are standard reports |
| `dataio.import` | `dataio.import.create` | "Importar" |
| `<m>.write` (assets, crm, hr, inventory, onboarding, purchasing, reporting) | `<m>.*.create`, `<m>.*.update` | quick actions "Nuevo …" |

Tests: `backend/iam/tests/test_fix_ui_login_w03.py` (every registry code resolvable; catalog role-template matrix; later
modules appear with a synthetic catalog; deny-by-default rules).
