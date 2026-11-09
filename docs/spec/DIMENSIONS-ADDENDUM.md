# DIMENSIONS-ADDENDUM — accounting dimension masters: Centros de costo, Unidades de negocio, Auxiliares abiertos, Ítems contables (binding; 2.1.1-dev, amended 2.2.0-dev)

**Status: BINDING requirement change** (new in 2.1.1-dev; requested by the user: "Add the following accounting dimensions as masters: Auxiliar Abierto (code, description,
Agrupador/Detalle, table and tree view), Item (code, description, Agrupador/Detalle, table and tree view)"). M5 records it as **CCR-DIM-001** in the G0 attempt (or at adoption,
see MIGRATION.md). Machine-readable twin: `ui/dimensions.json` (authoritative for field names, rules, task mapping). Validator: `tools/uictl.py validate` (dimension checks included).

**Precedence:** original prompt → DEV-ADDENDUM → UI-CONTRACT → this addendum (for the two dimensions only). It never relaxes security, in-line synchronous ledger/stock posting,
Colombian rules, DEV-only rules or the protocol. The 24 original prompts stay byte-identical; `plan.json`, queues, machines and roles are unchanged (5 machines: M1–M4 workers, M5).

**Scope (amended by CCR-FEAT-001, 2.2.0-dev, user-approved):** four masters share every rule of this addendum: **Centro de costo** (`cost_center`, slot `cost_center_id`),
**Unidad de negocio** (`business_unit`, slot `business_unit_id`), **Auxiliar abierto** (`aux_abierto_id`) and **Ítem contable** (`item_dimension_id`). The P04 `CostCenter`
entity is superseded: the cost-center master lives in the P05 dimension framework and P04 role scopes reference it. Branches are specified in FEATURES-ADDENDUM F-ACC-BRANCH.
Wherever the text below names the two 2.1.1 dimensions, it applies equally to all four. Feature tests: FEATURES-ADDENDUM F-ACC-DIM (T-ACC-DIM-01…12).

## 1. Meaning and naming (accepted by the user in 2.2.0-dev)
* **Centro de costo** and **Unidad de negocio**: standard analytical masters per company.
* **Auxiliar abierto** (`aux_abierto`, line slot `aux_abierto_id`): a user-defined, open analytical code attached to accounting lines (for example project, contract, fund or campaign).
  It is **not** the general-ledger auxiliary report ("auxiliar por cuenta/tercero") and **not** AR/AP open items.
* **Ítem contable** (`item_contable`, line slot `item_dimension_id`): a user-defined accounting analysis item (for example a budget or expense line). It is **not** the inventory
  product (`inventory.Item`, and the existing `JournalLine.item` slot keeps meaning *product*) and **not** a PUC account.
* Labels are configurable per tenant (`label_es`). Codes are not personal data; users must not put PII in codes.

## 2. Master data (both dimensions, identical structure — `ui/dimensions.json › common_fields`)
`company_id` (empresa, the business entity — not the tenant; immutable), `code` (required, 1–40, unique per company and dimension, case-insensitive), `description` (required, 1–200),
`node_type` ∈ {**AGRUPADOR** (group, non-posting), **DETALLE** (detail, posting leaf)}, `parent_id` (an AGRUPADOR of the same company and dimension; null = root),
`valid_from`, `valid_to`, `status` ∈ {ACTIVE, ARCHIVED}, `sort_key`, `external_ref`, `version`. Every change is audited (P07 audit log).

## 3. Hierarchy rules (DIM-R01…R07)
1. Only DETALLE nodes are postable; AGRUPADOR references fail with `dimension_node_not_postable`.
2. Strict tree: one parent; no cycles; the parent must be an AGRUPADOR (same company and dimension); a DETALLE has no children.
3. Depth limit configurable per company and dimension (default 6, allowed 1–10).
4. Company scope through composite FKs `(company_id, id)`: no cross-company references (`dimension_cross_company`). The same code may exist in two companies.
5. Once referenced by a posted line: no delete, no node_type change, no company change; archive and description edits are allowed (audited); archived/expired nodes reject new references.
6. Re-parenting is audited and never rewrites posted lines. Reports group by an effective-dated closure table (default current hierarchy; option "as of period end"). Totals never change, only grouping.
7. Aggregation over a group = Σ descendant leaves, each leaf once (selecting a group and its child does not double count).

## 4. Posting integration (P05 owner; propagated by P10, P11, P13, P14, P16)
* `JournalLine` slots `cost_center_id` (now an FK to the cost-center master), `business_unit_id`, `aux_abierto_id` and `item_dimension_id` (nullable, composite FK with `company_id`). `ledger_balance.dims_hash` includes them.
* **Rule matrix** `DimensionRule` per company: for each dimension × (account or account range, document type, concept, journal) → allowed / required / forbidden. It extends
  `Account.requires_dimensions[]` and is checked by `PostingService.post` **inside the same UoW** (`dimension_required`, `dimension_forbidden`). `simulate`/preview returns the same errors.
* Amounts, balances and invariants are unaffected by dimension values (property test). Stock valuation is unchanged; stock-document postings that go through P05 accept the slots via
  defaults or rules (P09/P12 are not changed by this addendum).
* Source documents propagate the slots to their lines (sales, purchasing, payroll, assets, treasury). Reversal entries copy them.

## 5. API, UI, import/export, AI/MCP
* API (P02 contract at G0, P05 implementation): `/api/v1/ledger/dimensions/aux-abierto` and `/api/v1/ledger/dimensions/item-contable` with list (search, filters, sort, cursor),
  tree (`?root=&depth=&valid_on=`), get, create, patch (If-Match), `:archive`, `:move`, `:import`, `:export`, `/{id}/usage`. Every action idempotent and policy-checked.
* UI: Contabilidad › **Dimensiones contables** (`/m/contabilidad/dimensiones`) with the tabs **Centros de costo** (`…/centros-costo`), **Unidades de negocio** (`…/unidades-negocio`), **Auxiliares abiertos** (`…/auxiliares-abiertos`) and **Ítems contables** (`…/items-contables`).
  Each tab offers a **table view** and a **tree view** of the same data using P08's shared `TreeTable` (WAI-ARIA tree, keyboard navigation, search with path, sort, inline edit,
  Agrupador/Detalle badge, archive, move). A detail picker on document lines shows only postable, active leaves of the current company.
* P18 templates: code, description, node_type, parent_code, valid_from, valid_to, status. Two-pass parent resolution (children may come before parents). Cycles, cross-company parents or invalid
  parent types reject the whole batch. Export → re-import is a no-op (non-lossy).
* P04 permissions `ledger.dimension.read|write|admin`, optional subtree grants. These apply to API, UI, search, export, reports, AI (P19, proposals need confirmation) and MCP (P20, scoped OAuth).
* P17 semantic dimensions with hierarchy, trial balance / auxiliary by account × dimension, filters by subtree, as-of hierarchy option.

## 6. Task mapping (no plan change; rows also in `ui/task-matrix.json` marked `[DIM]`)
P01 (M1) slot registry/date helper · P02 (M2) contracts · P04 (M2) permissions · **P05 (M1) masters + posting** · **P08 (M4) TreeTable** · P10 (M1), P11 (M2), P13 (M4), P14 (M2),
P16 (M3) line propagation · **P17 (M2) reports** · P18 (M3) import/export · P19 (M2) AI · P20 (M1) MCP · P21 (M4) onboarding/seed · P22a–d (M5) tests · P23 (M4) packet statement.
Dependencies stay as in the plan: P05/P04/P08 before G1. Consumers code against the frozen contract with test doubles until accepted (PROTOCOL §4).

## 7. Dev-only and data rules
Synthetic demo nodes only (P21 seed: two levels in both demo companies). No production/staging. No real personal data in codes or descriptions.

## 8. Required product tests (to be written and run by the owners and M5 during the run — none exist yet)
| ID | Owner | Test |
|---|---|---|
| T-DIM-01 | P05 | Create AGRUPADOR + DETALLE in both dimensions; code unique per company (case-insensitive); same code allowed in another company |
| T-DIM-02 | P05 | Posting a line to an AGRUPADOR is rejected (`dimension_node_not_postable`) |
| T-DIM-03 | P05 | Cycle, DETALLE-as-parent and cross-dimension parent rejected |
| T-DIM-04 | P05 | Depth limit enforced on create and on move |
| T-DIM-05 | P05 | Cross-company reference rejected at API and at DB level (composite FK) |
| T-DIM-06 | P05 | Delete or node_type change of a used node rejected; archive/valid_to blocks new references only |
| T-DIM-07 | P05/P17 | Re-parent leaves posted lines unchanged; current vs as-of hierarchy give the same totals with different grouping |
| T-DIM-08 | P17 | Group + child filter selected together: no double counting |
| T-DIM-09 | P05 | Rule matrix required/forbidden checked in the same transaction; simulate returns the same errors; failure rolls back the whole document |
| T-DIM-10 | P05 | Property test: amounts, balances, invariants 1/2 unchanged by any dimension assignment |
| T-DIM-11 | P18 | Import with children before parents OK; cycle file rejected atomically; export → import is a no-op |
| T-DIM-12 | P04/P19/P20 | Subtree grant limits list/tree/search/export/reports/AI/MCP |
| T-DIM-13 | P10/P11 | Propagation PO → receipt → invoice → journal, and sales invoice → journal |
| T-DIM-14 | P08 | TreeTable keyboard/ARIA tree and 10,000-node virtualization |

## 9. Gate items
G0 contracts + CCR-DIM-001 · G1 P05/P04/P08 parts accepted · G2 propagation + S-DIM · G3 reports, import/export, AI/MCP, seed, leak suite · G4 E2E/a11y + traceability.
