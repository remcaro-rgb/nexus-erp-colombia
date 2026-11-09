# FEATURES-ADDENDUM — mandatory feature specification (binding, 2.2.0-dev)

**Status: BINDING** (CCR-FEAT-001, recorded by M5 in the G0 attempt or at adoption). Authoritative machine-readable twin: `features/feature-registry.json`;
validator `tools/featctl.py validate`; defaults in `DECISIONS-DEFAULTS.md`. The user asked that every listed feature be fully defined and mandatory, and that agents
execute without asking. Every feature below is **mandatory [P]**; its tests are acceptance criteria of the named tasks and gates. Original-pack items marked `[A]` that are
not in this document stay optional. The 24 original prompts stay byte-identical; where this addendum says **Supersedes**, the new text wins for that feature.

**Never relaxed:** tenant/company isolation, row/field policy in API/UI/export/report/AI/MCP/portals, in-line synchronous ledger and stock posting in one transaction
(no partial ledger, no negative stock unless explicitly allowed), immutability of posted documents (reversal only), Colombian rules with unknown parameters failing closed,
dev-only runtime (no production/staging, synthetic data, no live DIAN/bank/payment/e-mail/SMS/LLM), and the five-machine protocol.

**Common rules for every document in this addendum:** Idempotency-Key on every mutating API call; optimistic locking (If-Match/version); `:simulate` returns the exact
journal/stock preview of `:post` with the same validation errors; posting allocates numbers, moves stock, values layers and posts the journal in ONE database transaction
(row locks on quants/open items; deadlock retry from P01); failures roll back everything; every state change is audited (P07); every screen follows UI-CONTRACT
(list → detail → form → preview → approval → post → print/export → history) and is keyboard and screen-reader usable; amounts and quantities are Decimal (D-001).

## 0. Index

| Feature | Owner (machine) | Proven at | Audit rows | Tests |
|---|---|---|---|---|
| F-ACC-DIM — Dimensiones analíticas: centros de costo, unidades de negocio, auxiliares abiertos, ítems contables | P05 (M1) | G1 | A2, A2.1, A2.2, A2.3, A2.4, A2.5, A4, A5a, A5a.1, A5a.2, A5a.3, A5a.4, A5a.5, A5a.6, A5a.7, A5b, A5b.1, A5b.2, A5b.3, A5b.4, A5b.5, A5b.6, A5b.7 | 12 |
| F-ACC-BRANCH — Sucursales por empresa | P04 (M2) | G1 | A3, A3.1, A3.2, A3.3 | 6 |
| F-ACC-EXO — Información exógena: motor de formatos versionados, mapeo, validación y archivo | P17 (M2) | G3 | A1, A1.1, A1.2 | 6 |
| F-ACC-REPORTS — Estados e informes contables | P17 (M2) | G3 | A6, A6.1, A6.2, A6.3, A6.4, A6.5, A6.6 | 7 |
| F-TRE-RC — Recibos de caja | P16 (M3) | G2 | T1, T1.1, T1.2, T1.3 | 8 |
| F-TRE-CE — Comprobantes de egreso | P16 (M3) | G2 | T2, T2.1, T2.2, T2.3 | 7 |
| F-INV-ISSUE — Salidas de inventario por concepto | P09 (M1) | G2 | I1, I1.1, I1.2, I1.3, I1.4 | 5 |
| F-INV-PRES — Presentaciones de producto y stock por presentación | P09 (M1) | G2 | I2 | 5 |
| F-INV-LOC — Ubicaciones dentro de bodegas y stock por ubicación | P09 (M1) | G2 | I3, I3.1, I3.2, I3.3 | 5 |
| F-INV-BARCODE — Generación e impresión de códigos de barras | P09 (M1) | G2 | I4, I4.1, I4.2, I4.3 | 6 |
| F-INV-BOM — Recetas y listas de materiales | P09 (M1) | G2 | I5, I5.1, I5.2 | 4 |
| F-INV-XFORM — Motor de transformaciones (presentación, porcionado, manual) | P09 (M1) | G2 | I6, I7, I8 | 9 |
| F-INV-REQ — Solicitudes internas de mercancía, consolidación, traslado automático y/o compra | P11 (M2) | G2 | I9, I9.1, I9.2, I9.3, I9.4 | 7 |
| F-PUR-CYCLE — Ciclo completo de compras con cotizaciones | P11 (M2) | G2 | I10, I10.1, I10.2, I10.3, I10.4, I10.5, I10.6, I10.7 | 8 |
| F-FORMULA-ENGINE — Motor seguro de fórmulas (DSL decimal) | P07 (M4) | G1 | — (enabler) | 6 |
| F-SAL-PRICING — Precios formulados de productos y servicios | P10 (M1) | G2 | V1 | 5 |
| F-PT-OPEN — Arquitectura abierta para proveedores tecnológicos | P15 (M3) | G2 | V2 | 2 |
| F-PAY-FORMULA — Conceptos de nómina formulados (devengos y deducciones) | P13 (M4) | G2 | N1, N1.1, N1.2, N1.3 | 5 |
| F-PORTAL-CORE — Base común de portales (identidad externa, invitaciones, shell) | P04 (M2) | G1 | — (enabler) | 5 |
| F-PORTAL-SUP — Portal de autoservicio de proveedores | P11 (M2) | G2 | P1 | 6 |
| F-PORTAL-CUS — Portal de autoservicio de clientes | P10 (M1) | G2 | P2 | 6 |
| F-PORTAL-EMP — Portal de autoservicio de empleados | P13 (M4) | G2 | P3 | 4 |
| F-X-AI-MCP — IA y MCP sobre todas las funciones nuevas | P19 (M2) | G3 | — (enabler) | 4 |
| F-X-DATAIO — Importación/exportación de los nuevos maestros y documentos | P18 (M3) | G3 | — (enabler) | 2 |
| F-X-SEED — Datos sintéticos para todas las funciones y KPIs | P21 (M4) | G3 | — (enabler) | 2 |
| F-X-QA — Escenarios transversales, seguridad, E2E, visual y accesibilidad | P22d (M5) | G4 | — (enabler) | 7 |
| F-X-COMPLIANCE — Etiquetas de no certificación y entradas legales externas | P23 (M4) | G4 | — (enabler) | 2 |
| F-X-RUNTIME — Evidencia de ejecución en DEV de todas las funciones | DEV (M5) | DEV | — (enabler) | 3 |

## 1. F-ACC-DIM — Dimensiones analíticas: centros de costo, unidades de negocio, auxiliares abiertos, ítems contables
*Analytical dimension masters (cost centers, business units, open auxiliaries, accounting items).* Owner **P05** (M1); proven at **G1**. Audit rows: A2, A2.1, A2.2, A2.3, A2.4, A2.5, A4, A5a, A5a.1, A5a.2, A5a.3, A5a.4, A5a.5, A5a.6, A5a.7, A5b, A5b.1, A5b.2, A5b.3, A5b.4, A5b.5, A5b.6, A5b.7.

* **Contributors:** P02: contracts at G0 (4 master APIs, line slots, error codes); P04: permissions ledger.dimension.*, role scope by cost-center subtree via kernel.interfaces.Dimensions; P08: shared TreeTable; P17: reports by dimension; P18: import/export templates; P19: AI tools; P20: MCP tools; P21: synthetic trees
* **Supersedes:**
  * P04 `CostCenter` (hierarchical) entity and `/api/v1/iam/cost-centers`: the cost-center master now lives in the P05 dimension framework (`/api/v1/ledger/dimensions/cost-centers`); P04 keeps only role scopes that reference cost-center ids.
  * shared/06 'BC (business units)' [A]: business units are now a mandatory master (dimension `business_unit`).
  * DIMENSIONS-ADDENDUM 2.1.1 scope note 'does not change cost centers': superseded — all four masters share the same invariants.
* **Data model:**
  * `DimensionNode`(id, company_id FK iam.Company, dimension ∈ {cost_center, business_unit, aux_abierto, item_contable}, code 1–40 unique per (company, dimension) case-insensitive, description 1–200, node_type ∈ {AGRUPADOR, DETALLE}, parent_id → same company+dimension AGRUPADOR, valid_from, valid_to, status ∈ {ACTIVE, ARCHIVED}, sort_key, external_ref, version)
  * `DimensionClosure`(ancestor_id, descendant_id, depth, valid_from, valid_to) effective-dated; rebuilt in the same transaction as a move
  * `DimensionRule`(company, dimension, scope_type ∈ {account, account_range, document_type, concept, journal, stock_document}, scope_ref, mode ∈ {REQUIRED, ALLOWED, FORBIDDEN}, priority)
  * `JournalLine` slots `cost_center_id`, `business_unit_id`, `aux_abierto_id`, `item_dimension_id` (nullable, composite FK (company_id, id)); `ledger_balance.dims_hash` includes all four
  * `DimensionSettings`(company, dimension, max_depth default 6)
* **States and transitions:**
  * Node: ACTIVE → ARCHIVED (archive; blocks new references after the archive date) → ACTIVE (unarchive, if parent ACTIVE)
  * Move: validate (cycle, depth, parent type, same company) → closure rebuild → audit entry; posted lines unchanged
* **API (P02 freezes the contract at G0):**
  * GET/POST `/api/v1/ledger/dimensions/{cost-centers|business-units|aux-abierto|item-contable}` (list: q, node_type, status, parent_id, valid_on, sort code|description|path, cursor)
  * GET `…/{dim}/tree?root=&depth=&valid_on=` · GET/PATCH `…/{dim}/{id}` (If-Match) · POST `…/{id}:archive`, `:unarchive`, `:move` · GET `…/{id}/usage` · POST `…:import` · GET `…:export`
  * GET/PUT `/api/v1/ledger/dimension-rules` · POST `/api/v1/ledger/journal-entries:simulate` returns dimension errors
* **Errors (stable codes):**
  * dimension_node_not_postable
  * dimension_required
  * dimension_forbidden
  * dimension_cross_company
  * dimension_cycle
  * dimension_depth_exceeded
  * dimension_in_use
  * dimension_archived
  * dimension_code_duplicate
* **Posting:**
  * No new accounts; the slots ride on every journal line produced by any module (sales, purchasing, payroll, assets, treasury, inventory issues/transformations). Amounts never change with dimension values.
* **Security:**
  * Row policy: company scope; optional subtree grants (role sees only a subtree) applied in list/tree/search/export/report/AI/MCP
  * Field policy unchanged for amounts; dimension codes are not personal data (users must not enter PII)
* **UI:**
  * Contabilidad › Dimensiones (tabs Centros de costo · Unidades de negocio · Auxiliares abiertos · Ítems contables); each tab: table view + tree view of the same data (TreeTable), search with path, sort, inline edit, Agrupador/Detalle badge, archive/move dialogs with usage count, import/export buttons
  * Line pickers show only ACTIVE DETALLE nodes of the current company valid on the document date
* **AI / MCP:**
  * P19 read/search tools + proposals (create/move/archive) that require user confirmation
  * P20 MCP read/search with scope ledger.dimension.read
* **Import / export:**
  * P18 templates: dimension, code, description, node_type, parent_code, valid_from, valid_to, status; two-pass parent resolution; cycles/cross-company rejected atomically; export→import no-op
* **Reports:**
  * Trial balance / auxiliary by account × any dimension (subtree filter, as-of hierarchy option)
* **Defaults:** D-007, D-030 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P04 via kernel.interfaces.Dimensions (cost-center lookup for role scopes) → G1; P08 via frontend design-system TreeTable/EntityForm contract (UI-CONTRACT, frozen at G0) → G1
* **Code locations (inside the owners' plan paths):** P05: `backend/ledger/dimensions/**`, `frontend/src/features/ledger/dimensions/**`; P08: `frontend/src/design-system/tree-table/**`; P04: `backend/iam/scopes/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-ACC-DIM-01 | P05 | unit | G1 | Create AGRUPADOR+DETALLE in each of the 4 dimensions; code unique per company case-insensitive; same code allowed in another company |
| T-ACC-DIM-02 | P05 | integration | G1 | Posting to an AGRUPADOR fails `dimension_node_not_postable`; whole document rolled back |
| T-ACC-DIM-03 | P05 | unit | G1 | Cycle, DETALLE-as-parent, cross-dimension parent and depth > limit rejected on create and on move |
| T-ACC-DIM-04 | P05 | integration | G1 | Cross-company reference rejected by API and by DB composite FK |
| T-ACC-DIM-05 | P05 | integration | G1 | Used node: delete and node_type change rejected; archive blocks new references only |
| T-ACC-DIM-06 | P05 | integration | G1 | Rule matrix REQUIRED/FORBIDDEN per account/document/concept enforced in the posting transaction; simulate returns identical errors |
| T-ACC-DIM-07 | P05 | property | G1 | Random dimension assignment never changes amounts, balances or invariants 1/2 |
| T-ACC-DIM-08 | P05 | e2e | G1 | UI: table↔tree toggle, create group+leaf, move, archive in each of the 4 tabs; keyboard tree navigation (role=tree) |
| T-ACC-DIM-09 | P04 | integration | G1 | Subtree grant on cost centers limits list/tree/search/export results; role scope by cost center works after G1 wiring |
| T-ACC-DIM-10 | P17 | integration | G3 | Group+child filter selected together: no double counting; as-of vs current hierarchy same totals |
| T-ACC-DIM-11 | P18 | integration | G3 | Import children-before-parents OK; cycle file rejected with zero rows applied; export→import no-op |
| T-ACC-DIM-12 | P08 | unit | G1 | TreeTable: 10,000 nodes virtualized, ARIA tree keyboard, table/tree same dataset |

## 2. F-ACC-BRANCH — Sucursales por empresa
*Branches per company (explicit FK, CRUD, links).* Owner **P04** (M2); proven at **G1**. Audit rows: A3, A3.1, A3.2, A3.3.

* **Contributors:** P09: Warehouse.branch_id required FK; P12: PosConfig.branch_id required FK; P17: branch filter in all reports; P18: import template; P21: 2 synthetic branches per demo company
* **Supersedes:**
  * P04 `Branch` (no fields listed): now fully specified below.
* **Data model:**
  * `Branch`(id, company_id FK NOT NULL, code unique per company, name, address, municipality (DANE code), phone, default_business_unit_id (nullable, same company), is_head_office (exactly one per company), status ACTIVE/ARCHIVED, valid_from, valid_to)
  * `Warehouse.branch_id` and `PosConfig.branch_id` must reference a branch of the same company (composite FK)
* **States and transitions:**
  * ACTIVE → ARCHIVED only when no ACTIVE warehouse/POS references it (error `branch_in_use`)
* **API (P02 freezes the contract at G0):**
  * GET/POST `/api/v1/iam/companies/{company_id}/branches`, GET/PATCH `/api/v1/iam/branches/{id}`, POST `…:archive`
* **Errors (stable codes):**
  * branch_code_duplicate
  * branch_cross_company
  * branch_in_use
  * branch_head_office_required
* **Posting:**
  * N/A as a document: branch_id is stamped by the owning documents on their headers and journal lines in the same transaction; posting refuses an archived or other-company branch (BRANCH_ARCHIVED, BRANCH_COMPANY_MISMATCH).
* **Security:**
  * Company admins manage branches of their company; role scopes by branch (existing)
* **UI:**
  * Administración › Empresas (tabs Empresas · Sucursales): table with filters, form with municipality picker, archive with dependency list
* **AI / MCP:**
  * P19/P20 read tools
* **Import / export:**
  * P18 branch template
* **Reports:**
  * Branch filter/grouping in P17 standard reports and KPIs
* **Defaults:** D-030 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P04: `backend/iam/branches/**`, `frontend/src/features/iam/branches/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-ACC-BRANCH-01 | P04 | integration | G1 | Branch CRUD; code unique per company; same code in another company allowed; exactly one head office |
| T-ACC-BRANCH-02 | P04 | integration | G1 | Cross-company warehouse/POS link rejected (API + composite FK); archive blocked while referenced |
| T-ACC-BRANCH-03 | P09 | integration | G2 | Warehouse requires a same-company branch |
| T-ACC-BRANCH-04 | P12 | integration | G2 | PosConfig requires a same-company branch |
| T-ACC-BRANCH-05 | P17 | integration | G3 | Report filtered by branch equals the sum of its warehouses/POS documents |
| T-ACC-BRANCH-06 | P22c | e2e | G3 | UI: Administración › Empresas › Sucursales list/create/edit/archive with dependency list; keyboard + labels |

## 3. F-ACC-EXO — Información exógena: motor de formatos versionados, mapeo, validación y archivo
*Exógena engine: versioned format packs, mapping, validation, file export.* Owner **P17** (M2); proven at **G3**. Audit rows: A1, A1.1, A1.2.

* **Contributors:** P06: format-pack schema + loader + synthetic pack SYN-EXO-1001 + validation rule engine; P18: import of format packs and concept mappings; P23: states that real DIAN packs are an external legal input
* **Supersedes:**
  * P17 'información exógena formats generated from the current format specifications': the generator is mandatory and complete as an engine; real yearly DIAN packs remain external (B-14) and are never invented.
* **Data model:**
  * `ExogenaFormatPack`(year, format_code, version, source ∈ {SYNTHETIC, OFFICIAL_IMPORTED}, legal_status ∈ {NOT_DIAN_VALID, PENDING_VERIFICATION, VERIFIED_BY_USER}, schema JSON (fields: name, type, length, required, enum, rule refs), file_spec (XML/TXT layout, encoding, header), checksum)
  * `ExogenaConceptMap`(company, pack, concept_code ↔ account set/ranges + party role + amount basis)
  * `ExogenaRun`(company, pack, year, status, rows, validation_report, file hash, generated_by, generated_at) — immutable after GENERATED
* **States and transitions:**
  * Run: DRAFT → MAPPED → VALIDATED (0 blocking errors) → GENERATED (file + hash) → SUPERSEDED (by a later run)
  * Validation failure keeps VALIDATION_FAILED with row-level errors
* **API (P02 freezes the contract at G0):**
  * GET/POST `/api/v1/tax/exogena/format-packs` (P06; import JSON pack, schema-validated)
  * GET/PUT `/api/v1/reporting/exogena/mappings`
  * POST `/api/v1/reporting/exogena/runs` · POST `…/{id}:validate` · POST `…/{id}:generate` · GET `…/{id}/file` · GET `…/{id}/report`
* **Errors (stable codes):**
  * exogena_pack_invalid
  * exogena_mapping_incomplete
  * exogena_party_data_missing
  * exogena_pack_not_verified (warning in dev; blocks files labelled official)
* **Posting:**
  * None (reporting only).
* **Security:**
  * Permission reporting.exogena.*; party tax ids are restricted fields; files stored with hash and access log
* **UI:**
  * Contabilidad › Impuestos › Exógena: pack list with legal-status badge, mapping editor, run wizard (map → validate → generate), row error table with drill-down to journal lines, download
* **AI / MCP:**
  * AI may explain validation errors; cannot generate or download files without the user's confirmation
* **Import / export:**
  * P18 imports format packs and mappings
* **Reports:**
  * Exógena run report + reconciliation: Σ reported amounts per concept = Σ mapped ledger balances
* **External inputs (never invented; do not block DEV):**
  * Official yearly DIAN format packs and validation rules (B-14) — external legal input; dev uses SYN-EXO-1001 marked NOT DIAN-VALID
* **Defaults:** D-019 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P18 via dataio ExportFormat registry (fixed-width/XML/CSV writer interface frozen at G0) → G3
* **Code locations (inside the owners' plan paths):** P06: `backend/tax/exogena/**`; P17: `backend/reporting/exogena/**`, `frontend/src/features/reporting/exogena/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-ACC-EXO-01 | P06 | unit | G1 | Format pack schema validation rejects missing fields/unknown types; synthetic pack loads with legal_status NOT_DIAN_VALID |
| T-ACC-EXO-02 | P17 | integration | G3 | Run over the synthetic dataset: mapping → validation → file; Σ file amounts per concept = Σ ledger balances |
| T-ACC-EXO-03 | P17 | integration | G3 | Row-level validation errors (missing NIT/DV, negative amount, unmapped account) block generation |
| T-ACC-EXO-04 | P17 | unit | G3 | Generated file is byte-identical across two runs on the same data (deterministic, hashed) |
| T-ACC-EXO-05 | P17 | e2e | G3 | UI wizard and download; badge 'SINTÉTICO — no válido ante la DIAN' visible on pack and file name |
| T-ACC-EXO-06 | P23 | doc | G4 | Compliance packet lists official packs as an external input; no claim of DIAN validity |

## 4. F-ACC-REPORTS — Estados e informes contables
*Financial statements and auxiliary reports.* Owner **P17** (M2); proven at **G3**. Audit rows: A6, A6.1, A6.2, A6.3, A6.4, A6.5, A6.6.

* **Contributors:** P05: ledger queries and drill-down source; P18: export formats; P19: AI explanations
* **Supersedes:**
  * P17 standard reports [P] list: each of the six user reports now has a named screen, API, filters, drill-down, export and test (below).
* **Data model:**
  * `ReportDefinition` for: balance_general, estado_resultados (by function and by nature), flujo_efectivo (indirect, D-006), estados_financieros (package: the three + cambios en el patrimonio), auxiliar_cuenta, auxiliar_centro_costo
  * `CashFlowMapping`(company, account range → line, sign)
* **States and transitions:**
  * Report run: requested → running → ready | failed (retry); saved report definitions draft → published → archived; generated snapshots are immutable and carry the ledger cut-off sequence.
* **API (P02 freezes the contract at G0):**
  * GET `/api/v1/reporting/standard/{balance-general|estado-resultados|flujo-efectivo|estados-financieros|auxiliar-cuenta|auxiliar-centro-costo}` with filters company, branch, period from/to, comparison period, book, dimension subtree, currency, include zero; `?format=json|csv|xlsx|pdf`
  * GET `…/{report}/drill?line=` → journal lines → source document
* **Errors (stable codes):**
  * report_period_open_warning (non-blocking)
  * report_mapping_incomplete (cash flow)
* **Posting:**
  * Read-only: reports read posted ledger data only (never drafts), never post or mutate; closing entries are produced by P05 period close, not by reports.
* **Security:**
  * Row policy by company/branch/cost-center subtree; export logged; masked fields stay masked
* **UI:**
  * Reportes › Estados (tabs Balance general · Estado de resultados · Flujo de efectivo · Estados financieros) and Reportes › Auxiliares (tabs Por cuenta · Por centro de costo · Por tercero): filter bar, comparative columns, drill-down drawer, export menu, print
* **AI / MCP:**
  * AI answers 'por qué cambió X' with cited report lines; read-only
* **Import / export:**
  * Exports CSV/XLSX/PDF via P17 documents
* **Reports:**
  * Six reports listed; all reconcile to the trial balance
* **Defaults:** D-006 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P18 via dataio ExportFormat registry (XLSX/CSV/PDF export interface frozen at G0) → G3
* **Code locations (inside the owners' plan paths):** P17: `backend/reporting/standard/**`, `frontend/src/features/reporting/standard/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-ACC-REPORTS-01 | P17 | integration | G3 | Balance general: assets = liabilities + equity on S1–S12; comparative period columns |
| T-ACC-REPORTS-02 | P17 | integration | G3 | Estado de resultados (function and nature) net = balance current-year result |
| T-ACC-REPORTS-03 | P17 | integration | G3 | Flujo de efectivo indirect ends at the bank/cash GL balance; unmapped accounts listed |
| T-ACC-REPORTS-04 | P17 | integration | G3 | Estados financieros package renders all statements consistently for one period |
| T-ACC-REPORTS-05 | P17 | integration | G3 | Auxiliar por cuenta: opening + movements = closing per account; drill to source document |
| T-ACC-REPORTS-06 | P17 | integration | G3 | Auxiliar por centro de costo: subtree filter, no double counting, ties to the trial balance by cost center |
| T-ACC-REPORTS-07 | P17 | e2e | G3 | Each of the six screens: filters, drill-down, export CSV/XLSX/PDF, print; row policy hides other branches |

## 5. F-TRE-RC — Recibos de caja
*Cash receipts (customer AR and non-AR concepts).* Owner **P16** (M3); proven at **G2**. Audit rows: T1, T1.1, T1.2, T1.3.

* **Contributors:** P10: AR open-item application interface (kernel.interfaces.ArOpenItems); P06: withholding calculation; P05: posting; P07: approvals; P17: PDF template via DocumentRenderer; P18: import of concepts; P10/portal: simulated payment intents feed DRAFT receipts
* **Supersedes:**
  * P10 `/receipts`: AR receipts are now created as RC treasury vouchers (P16) that call the P10 AR application interface; P10 keeps open-item logic and its existing receipt API as a thin alias to the RC voucher.
* **Data model:**
  * `TreasuryVoucher`(id, company, branch, type ∈ {RC, CE}, number (kernel.sequences RC/CE, assigned at POST), date, period, party (nullable for pure concept vouchers), currency + rate, fund_account (bank or cash box), payment_method ∈ {cash, transfer, cheque, card_record, consignment, other}, method_ref, status, total, memo, attachments, created_by, approved_by, posted_entry_id, reversal_of, version)
  * `TreasuryVoucherLine`(voucher, kind ∈ {AR_APPLICATION (P10 open item), AP_APPLICATION (P11 open item), ADVANCE (customer/vendor advance account), CONCEPT (TreasuryConcept), WITHHOLDING (P06 tax code), ROUNDING}, ref_open_item_id, concept_id, account (derived, never typed for AR/AP), amount, currency_amount, dims (4 analytical slots), tax_base)
  * `TreasuryConcept`(company, code, name, direction ∈ {IN, OUT, BOTH}, account, default dims, requires_party, withholding profile, approval_threshold, active)
  * `FundSnapshot`(fund_account, as_of, book_balance, unreconciled) shown at entry and stored on the voucher at POST
* **States and transitions:**
  * DRAFT → (submit) PENDING_APPROVAL (if amount ≥ concept/company threshold or CE always above COP 0 when policy says so) → APPROVED → (post) POSTED → (void) REVERSED by a reversal voucher in an open period
  * DRAFT → CANCELLED (never numbered). POSTED is immutable; edits only via reversal + new voucher.
* **API (P02 freezes the contract at G0):**
  * GET/POST `/api/v1/treasury/receipts` (filters: status, date, party, fund, number) · GET/PATCH `…/{id}` (DRAFT only, If-Match) · POST `…/{id}:submit`, `:approve`, `:reject`, `:post`, `:reverse`, `:simulate` (journal preview) · GET `…/{id}/pdf` · POST `…:bulk-print`
  * GET/POST `/api/v1/treasury/concepts` · GET `/api/v1/treasury/funds/{id}/snapshot`
  * All mutating calls require an Idempotency-Key
* **Errors (stable codes):**
  * treasury_period_closed
  * treasury_overapplication
  * treasury_open_item_mismatch_party
  * treasury_currency_mismatch
  * treasury_approval_required
  * treasury_insufficient_funds_warning (CE; blocking only if the fund forbids overdraft)
  * tax_parameter_unknown
  * idempotency_conflict
* **Posting:**
  * RC: Dr fund account (bank/cash) Σ received; Cr customer AR control per applied open item; Cr customer advances for unapplied amount; Dr withholding receivable (retención practicada por el cliente) per tax code; Cr concept accounts for CONCEPT lines (non-AR income, loans, capital, reimbursements)
  * Open items, advances and the journal entry are updated in ONE transaction with the voucher number allocation; any failure rolls back everything (no number gap, no partial ledger)
  * Reversal: mirror entry dated in an open period, re-opens applied items, links reversal_of; original number remains used
* **Security:**
  * Permissions treasury.receipt.{read,create,approve,post,reverse}; SoD: creator ≠ approver; fund access by role; amounts visible per field policy
* **UI:**
  * Tesorería › Recibos: list with status chips and totals, entry form (party, fund snapshot, AR items picker with partial amounts, advances, concept lines, withholding lines auto-proposed), journal preview, approval inbox link, PDF preview/print/download, reversal dialog, history timeline
* **AI / MCP:**
  * AI drafts a receipt from a bank line or text; user confirms; AI never posts
* **Import / export:**
  * Concept import; receipts export CSV/XLSX
* **Reports:**
  * Recaudos por periodo/fondo/concepto/cliente; reconciliation with bank statements by exact voucher id
* **Defaults:** D-017, D-029, D-021 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3
* **Code locations (inside the owners' plan paths):** P16: `backend/treasury/vouchers/**`, `frontend/src/features/treasury/vouchers/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-TRE-RC-01 | P16 | integration | G2 | RC applying two AR invoices partially + advance remainder + customer withholding: exact journal lines, open items updated, one transaction |
| T-TRE-RC-02 | P16 | integration | G2 | RC by concept without AR (e.g. loan received, reimbursement): posts to concept account with dims |
| T-TRE-RC-03 | P16 | concurrency | G2 | 50 concurrent RC posts: gap-free RC numbers, no double application of the same open item (row lock), idempotent retry returns the same voucher |
| T-TRE-RC-04 | P16 | integration | G2 | Failure in withholding calc or ledger post rolls back number, open items and entry (no partial ledger) |
| T-TRE-RC-05 | P16 | integration | G2 | Reversal re-opens items, mirror entry, original immutable; reversal in closed period rejected |
| T-TRE-RC-06 | P16 | integration | G2 | Approval threshold routes to P07 workflow; creator cannot approve |
| T-TRE-RC-07 | P17 | integration | G3 | RC PDF contains number, party, lines, totals in words (es-CO), fund, preparer/approver; deterministic hash |
| T-TRE-RC-08 | P22c | e2e | G3 | UI: create, approve, post, print, reverse an RC; keyboard and screen-reader labels |

## 6. F-TRE-CE — Comprobantes de egreso
*Disbursement vouchers (vendor AP and direct concepts).* Owner **P16** (M3); proven at **G2**. Audit rows: T2, T2.1, T2.2, T2.3.

* **Contributors:** P11: AP open-item interface + payment proposals; P06: withholding; P05: posting; P07: approvals; P13: payroll net pay payable (fake until G2); P17: PDF
* **Supersedes:**
  * P11/P16 payment batches stay; each executed payment now produces a CE voucher (one per payee) so every disbursement has a numbered comprobante.
* **Data model:**
  * `TreasuryVoucher`(id, company, branch, type ∈ {RC, CE}, number (kernel.sequences RC/CE, assigned at POST), date, period, party (nullable for pure concept vouchers), currency + rate, fund_account (bank or cash box), payment_method ∈ {cash, transfer, cheque, card_record, consignment, other}, method_ref, status, total, memo, attachments, created_by, approved_by, posted_entry_id, reversal_of, version)
  * `TreasuryVoucherLine`(voucher, kind ∈ {AR_APPLICATION (P10 open item), AP_APPLICATION (P11 open item), ADVANCE (customer/vendor advance account), CONCEPT (TreasuryConcept), WITHHOLDING (P06 tax code), ROUNDING}, ref_open_item_id, concept_id, account (derived, never typed for AR/AP), amount, currency_amount, dims (4 analytical slots), tax_base)
  * `TreasuryConcept`(company, code, name, direction ∈ {IN, OUT, BOTH}, account, default dims, requires_party, withholding profile, approval_threshold, active)
  * `FundSnapshot`(fund_account, as_of, book_balance, unreconciled) shown at entry and stored on the voucher at POST
  * `PaymentBatch` (existing P16) → generates CE vouchers on execution; bank file generation stays fixture/generic CSV (no live bank)
* **States and transitions:**
  * DRAFT → (submit) PENDING_APPROVAL (if amount ≥ concept/company threshold or CE always above COP 0 when policy says so) → APPROVED → (post) POSTED → (void) REVERSED by a reversal voucher in an open period
  * DRAFT → CANCELLED (never numbered). POSTED is immutable; edits only via reversal + new voucher.
* **API (P02 freezes the contract at G0):**
  * GET/POST `/api/v1/treasury/disbursements` (filters: status, date, party, fund, number) · GET/PATCH `…/{id}` (DRAFT only, If-Match) · POST `…/{id}:submit`, `:approve`, `:reject`, `:post`, `:reverse`, `:simulate` (journal preview) · GET `…/{id}/pdf` · POST `…:bulk-print`
  * GET/POST `/api/v1/treasury/concepts` · GET `/api/v1/treasury/funds/{id}/snapshot`
  * All mutating calls require an Idempotency-Key
  * POST `/api/v1/treasury/payment-batches/{id}:execute` → CE per payee (simulated bank file, FIXTURE_NOT_CERTIFIED)
* **Errors (stable codes):**
  * treasury_period_closed
  * treasury_overapplication
  * treasury_open_item_mismatch_party
  * treasury_currency_mismatch
  * treasury_approval_required
  * treasury_insufficient_funds_warning (CE; blocking only if the fund forbids overdraft)
  * tax_parameter_unknown
  * idempotency_conflict
* **Posting:**
  * CE: Cr fund account Σ paid; Dr vendor AP control per applied open item; Dr vendor advances; Cr withholding payable (retención practicada) per tax code; Dr concept accounts for direct disbursements (expenses, petty cash refill, loans, taxes)
  * Open items, advances and the journal entry are updated in ONE transaction with the voucher number allocation; any failure rolls back everything (no number gap, no partial ledger)
  * Reversal: mirror entry dated in an open period, re-opens applied items, links reversal_of; original number remains used
* **Security:**
  * treasury.disbursement.*; dual approval above company threshold (default COP 20,000,000); bank account changes of vendors require P11 verification (existing)
* **UI:**
  * Tesorería › Egresos (tabs Comprobantes · Lotes de pago): same document pattern as RC; batch execution creates numbered CEs; print/PDF per CE and bulk
* **AI / MCP:**
  * AI proposes CE drafts from approved payment proposals; user confirms
* **Import / export:**
  * Export CE register
* **Reports:**
  * Egresos por periodo/fondo/concepto/proveedor; cash forecast uses CE drafts
* **Defaults:** D-017, D-029 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3; P13 via kernel.interfaces.PayrollPayables → G2
* **Code locations (inside the owners' plan paths):** P16: `backend/treasury/vouchers/**`, `frontend/src/features/treasury/vouchers/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-TRE-CE-01 | P16 | integration | G2 | CE paying 3 vendor invoices partially with withholding: exact journal lines and AP open items |
| T-TRE-CE-02 | P16 | integration | G2 | Direct CE by concept (expense with cost center) without AP |
| T-TRE-CE-03 | P16 | integration | G2 | Payment batch execution → one CE per payee, numbers gap-free, simulated bank file labelled FIXTURE_NOT_CERTIFIED |
| T-TRE-CE-04 | P16 | integration | G2 | Dual approval above threshold; SoD enforced |
| T-TRE-CE-05 | P16 | integration | G2 | Reversal of a CE re-opens AP items; immutable original |
| T-TRE-CE-06 | P17 | integration | G3 | CE PDF deterministic with totals in words |
| T-TRE-CE-07 | P22c | e2e | G3 | UI: CE create/approve/post/print/reverse; batch execution flow |

## 7. F-INV-ISSUE — Salidas de inventario por concepto
*Stock issues by configurable concept (consumption, direct consumption, scrap, other).* Owner **P09** (M1); proven at **G2**. Audit rows: I1, I1.1, I1.2, I1.3, I1.4.

* **Contributors:** P05: account determination + dims; P07: approval policies; P17: issue report + PDF; P18: concept import
* **Supersedes:**
  * P09 `Scrap` and shared/07 'Consumption to cost center' rows: generalized into IssueConcept + StockIssue documents (scrap remains a seeded concept).
* **Data model:**
  * `IssueConcept`(company, code, name, kind ∈ {CONSUMPTION, DIRECT_CONSUMPTION, SCRAP, SAMPLE, DONATION, INTERNAL_USE, OTHER}, expense_account (or determination key), requires_cost_center, required_dims (rule-matrix link), requires_reason, approval_threshold_value, allow_negative=false, active)
  * `StockIssue`(company, branch, warehouse, concept, number (sequence SAL), date, requested_by, cost_center/business_unit/aux/item dims, reason, status, posted_entry)
  * `StockIssueLine`(item, presentation (optional), qty + uom, base_qty, location (leaf bin), lot/serial, unit_cost (from valuation at post), value, dims override)
  * Seeded concepts: CONSUMO (consumption to cost center), CONSUMO_DIRECTO (direct consumption charged to the cost of a job/project dimension), BAJA (scrap/write-off with reason codes), MUESTRA, OTRO
* **States and transitions:**
  * DRAFT → PENDING_APPROVAL (if concept/threshold requires) → APPROVED → POSTED (stock moves done + valuation + journal entry in one transaction) → REVERSED (by a reversal document; original immutable)
  * DRAFT/APPROVED → CANCELLED (releases reservations)
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/inventory/issue-concepts` · `/api/v1/inventory/issues` (+`:submit`, `:approve`, `:post`, `:reverse`, `:simulate`) · GET `…/{id}/pdf`
* **Errors (stable codes):**
  * stock_insufficient
  * issue_concept_inactive
  * dimension_required
  * issue_reason_required
  * lot_required
  * location_not_leaf
  * period_closed
  * idempotency_conflict
* **Posting:**
  * CONSUMO/CONSUMO_DIRECTO/OTRO: Dr concept expense account (with dims) / Cr inventory account of the item category, value = FIFO/average cost layers consumed
  * BAJA: Dr scrap expense (reason) / Cr inventory
  * Reversal: mirror entry + stock returned to the original bin/lot at the original cost
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
* **UI:**
  * Inventario › Movimientos › Salidas: list, entry with availability per bin/lot/presentation, cost preview, approval, post, print, reverse; Maestros › Conceptos de salida
* **AI / MCP:**
  * AI drafts issues from text; confirmation required
* **Import / export:**
  * Concept import/export; issues export
* **Reports:**
  * Salidas por concepto/centro de costo/periodo; kardex shows concept
* **Defaults:** D-032 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/issues/**`, `frontend/src/features/inventory/issues/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-ISSUE-01 | P09 | integration | G2 | Issue with each seeded concept posts the exact journal lines and stock moves; cost from layers |
| T-INV-ISSUE-02 | P09 | integration | G2 | Concept requiring cost center rejects missing dims; approval threshold routes to workflow |
| T-INV-ISSUE-03 | P09 | concurrency | G2 | Two concurrent issues of the last units: one succeeds, one fails stock_insufficient; no negative stock |
| T-INV-ISSUE-04 | P09 | integration | G2 | Reversal returns stock at original cost/bin/lot |
| T-INV-ISSUE-05 | P22c | e2e | G3 | UI: create/approve/post/print/reverse an issue per concept |

## 8. F-INV-PRES — Presentaciones de producto y stock por presentación
*Product presentations and stock per presentation.* Owner **P09** (M1); proven at **G2**. Audit rows: I2.

* **Contributors:** P10: sell by presentation; P11: buy by presentation; P12: POS scan by presentation barcode; P18: import
* **Supersedes:**
  * P09 `Item` base UoM + UoM conversions: kept; presentations are added as packaging identities (D-013) and are not equivalent to UoM conversions.
* **Data model:**
  * `Presentation`(company, item, code unique per item, name (e.g. 'Bolsa 500 g', 'Caja x12'), uom, factor_to_base Decimal > 0 exact, qty_precision, barcode(s) (unique per company), dimensions/weight, sellable, purchasable, default_for ∈ {sale, purchase, none}, active)
  * `PackagedQuant`(item, presentation, location, lot, qty_packages) — sub-ledger of `StockQuant`; invariant Σ(qty_packages × factor) ≤ StockQuant.qty_base (the rest is loose base stock)
  * Stock moves record presentation + package qty + base qty; valuation always in base qty
* **States and transitions:**
  * Presentation ACTIVE/ARCHIVED (archived only when no packaged stock)
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/inventory/items/{id}/presentations` · GET `/api/v1/inventory/availability?item=&presentation=&warehouse=&location=&lot=` returns base qty, packaged qty per presentation, loose qty
* **Errors (stable codes):**
  * presentation_factor_invalid
  * presentation_barcode_duplicate
  * packaged_exceeds_canonical
  * uom_precision_violation
* **Posting:**
  * No new postings: valuation in base qty; presentation is quantity identity only
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
* **UI:**
  * Inventario › Maestros › Productos › tab Presentaciones; Existencias › Por presentación (packaged + loose, per bin/lot)
* **AI / MCP:**
  * AI read tools
* **Import / export:**
  * Presentation import/export
* **Reports:**
  * Existencias por presentación; reconciliation report canonical vs packaged
* **Defaults:** D-013, D-001 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P11 via kernel.interfaces.Inventory (presentation-aware receive contract) → G2
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/presentations/**`, `frontend/src/features/inventory/presentations/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-PRES-01 | P09 | unit | G2 | Factor/precision validation; conversion exact with Decimal; rounding to presentation precision rejects fractional packages |
| T-INV-PRES-02 | P09 | property | G2 | For random receipts/issues/repacks: Σ packaged × factor ≤ canonical and total stock never double counted |
| T-INV-PRES-03 | P09 | integration | G2 | Availability by presentation/lot/bin; reservation of 2 boxes reserves 24 base units and 2 packages |
| T-INV-PRES-04 | P12 | integration | G2 | POS scan of a presentation barcode sells the package and decrements packaged + canonical stock |
| T-INV-PRES-05 | P22c | e2e | G3 | UI: create presentation, receive by presentation, see stock per presentation |

## 9. F-INV-LOC — Ubicaciones dentro de bodegas y stock por ubicación
*Bin locations inside warehouses, stock per bin.* Owner **P09** (M1); proven at **G2**. Audit rows: I3, I3.1, I3.2, I3.3.

* **Contributors:** P12: POS sells from the POS bin; P11: putaway at receipt; P18: import
* **Supersedes:**
  * P09 `Location/Bin` (no fields) and `available(item, warehouse)`: now an explicit tree inside each warehouse with per-bin availability.
* **Data model:**
  * `Location`(id, company, warehouse_id FK same company, parent_id (same warehouse), code unique per warehouse, name, type ∈ {VIEW, INTERNAL, TRANSIT, SCRAP}, is_leaf (derived), barcode, capacity (optional), allow_mixed_lots (default true), active)
  * Default tree per warehouse (D-014); `PutawayRule`(warehouse, item or category, target location, priority)
  * `Reservation`(item, presentation, location, lot, qty) — availability = on_hand − reserved per bin
* **States and transitions:**
  * Location ACTIVE/ARCHIVED (only when empty and no reservations); moves allowed only between leaf INTERNAL/TRANSIT/SCRAP bins
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/inventory/warehouses/{id}/locations` (+`/tree`) · GET `/api/v1/inventory/availability?location=` · POST `/api/v1/inventory/moves:internal` (bin-to-bin) · CRUD `/putaway-rules`
* **Errors (stable codes):**
  * location_not_leaf
  * location_cross_warehouse
  * location_not_empty
  * stock_insufficient
  * reservation_conflict
* **Posting:**
  * Bin-to-bin moves in the same warehouse: no journal entry (same valuation scope); cross-warehouse transfers follow P09 transfer posting
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
* **UI:**
  * Inventario › Maestros › Bodegas y ubicaciones: tree + table, capacity, barcode print; Existencias › Por ubicación; movement form 'Mover entre ubicaciones'
* **AI / MCP:**
  * AI suggests putaway; confirmation required
* **Import / export:**
  * Location tree import (two-pass parents)
* **Reports:**
  * Existencias por ubicación; ubicaciones vacías
* **Defaults:** D-014, D-032 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P11 via kernel.interfaces.Inventory (putaway/bin receive contract) → G2
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/locations/**`, `frontend/src/features/inventory/locations/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-LOC-01 | P09 | integration | G2 | Tree: no cycles, parent in same warehouse, posting only to leaf bins |
| T-INV-LOC-02 | P09 | integration | G2 | Availability and reservations per bin/lot; reservation prevents another issue from the same bin |
| T-INV-LOC-03 | P09 | integration | G2 | Receipt putaway uses rule, else RECEPCION; bin-to-bin move keeps valuation |
| T-INV-LOC-04 | P09 | concurrency | G2 | Concurrent moves out of one bin never go negative |
| T-INV-LOC-05 | P22c | e2e | G3 | UI: create bins, receive, move, see stock per bin |

## 10. F-INV-BARCODE — Generación e impresión de códigos de barras
*Barcode generation, validation, label printing and scanning.* Owner **P09** (M1); proven at **G2**. Audit rows: I4, I4.1, I4.2, I4.3.

* **Contributors:** P12: POS scanning; P08: scanner input + print preview components; P17: label PDF rendering (DocumentRenderer); P11: receiving scan
* **Supersedes:**
  * P17 'asset QR labels' only: product/presentation/location labels added. P09 barcode(s) storage kept.
* **Data model:**
  * `Barcode`(company, value unique per company, symbology ∈ {CODE128, EAN13, GTIN14_IMPORTED, QR}, owner_type ∈ {item, presentation, location, lot}, owner_id, source ∈ {GENERATED_INTERNAL, GENERATED_GS1_PREFIX, IMPORTED}, gs1_ownership_claimed=false unless source=GENERATED_GS1_PREFIX with a user-entered company prefix)
  * `LabelTemplate`(company, name, size mm (default 50×25), fields, symbology, copies) · `LabelPrintJob`(template, items/presentations/locations/lots, quantities, status, pdf hash)
* **States and transitions:**
  * Print job: QUEUED → RENDERED (PDF hash) → DOWNLOADED/PRINTED (browser print; no direct printer driver in dev)
* **API (P02 freezes the contract at G0):**
  * POST `/api/v1/inventory/barcodes:generate` (owner, symbology) · POST `…:validate` (check digit, uniqueness) · GET `/api/v1/inventory/barcodes/lookup?value=` (scan resolution: item/presentation/location/lot) · CRUD `/label-templates` · POST `/label-print-jobs` · GET `…/{id}/pdf` and `/preview.png`
* **Errors (stable codes):**
  * barcode_duplicate
  * barcode_checkdigit_invalid
  * gs1_prefix_missing
  * barcode_unknown
* **Posting:**
  * Labels and scans never post: a scan only fills a line of a document whose owner posts it (receipt, issue, transfer, POS sale); label print jobs are audited, not journaled.
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
  * Label PDFs never include cost
* **UI:**
  * Inventario › Maestros › Códigos y etiquetas: generate, validate, template designer (fixed fields), print preview, PDF download; scan input on receipts/issues/transfers/POS (keyboard-wedge scanner)
* **AI / MCP:**
  * None beyond read
* **Import / export:**
  * Import existing GTINs (validated, not claimed)
* **Reports:**
  * Productos sin código; duplicados
* **Defaults:** D-004, D-005 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3; P11 via kernel.interfaces.Labels (receipt label request) → G2
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/barcodes/**`, `frontend/src/features/inventory/barcodes/**`; P08: `frontend/src/design-system/scan-input/**`, `frontend/src/design-system/print-preview/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-BARCODE-01 | P09 | unit | G2 | Code128 encoder golden vectors and EAN-13 check digit (mod-10) golden vectors; GTIN validation |
| T-INV-BARCODE-02 | P09 | integration | G2 | Generated internal codes unique per company under 50 concurrent generations; GS1 generation refused without a user prefix |
| T-INV-BARCODE-03 | P09 | integration | G2 | Lookup resolves item/presentation/location/lot; unknown code returns barcode_unknown |
| T-INV-BARCODE-04 | P17 | integration | G3 | Label PDF contains the barcode image decodable back to the value (decoder test) and is deterministic |
| T-INV-BARCODE-05 | P12 | integration | G2 | POS scan input adds the right presentation line |
| T-INV-BARCODE-06 | P22d | visual | G4 | Label preview and print stylesheet screenshots match the approved baseline |

## 11. F-INV-BOM — Recetas y listas de materiales
*Recipes / bills of materials (versioned, yields, waste).* Owner **P09** (M1); proven at **G2**. Audit rows: I5, I5.1, I5.2.

* **Contributors:** P18: import; P17: costed BOM report
* **Supersedes:**
  * shared/06 'light manufacturing BoM/assembly [A]' and P09 'Kit/BOM (kits consumed at sale)': recipes/BOMs are mandatory; kits stay as a BOM type.
* **Data model:**
  * `Bom`(company, code, output item + presentation, type ∈ {KIT (consumed at sale), RECIPE/PRODUCTION (consumed by a transformation order)}, version, status ∈ {DRAFT, ACTIVE, OBSOLETE}, valid_from, valid_to, output_qty, expected_yield % , normal_waste %, byproducts[], notes)
  * `BomLine`(component item/presentation, qty per output batch, uom, scrap %, is_optional=false, substitute_group)
  * Invariants: no cycles across BOM components (graph check over ACTIVE versions), one ACTIVE version per output per date, effective-date ranges do not overlap
* **States and transitions:**
  * DRAFT → ACTIVE (validation: no cycles, components active, yields 0<y≤100) → OBSOLETE (superseded by a newer version or valid_to)
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/inventory/boms` (+`:activate`, `:obsolete`, `:copy-version`) · GET `/api/v1/inventory/boms/{id}/explode?qty=&date=` (multi-level, costed)
* **Errors (stable codes):**
  * bom_cycle
  * bom_version_overlap
  * bom_component_inactive
  * bom_yield_invalid
* **Posting:**
  * None by itself; consumed by transformation orders (INV-XFORM) and kit sales
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
* **UI:**
  * Inventario › Producción › Recetas/BOM: version list, editor with yield/waste, explosion preview with cost, activate with validation report
* **AI / MCP:**
  * AI drafts a recipe from text; confirmation required
* **Import / export:**
  * BOM import (header+lines)
* **Reports:**
  * Costo estándar por receta; uso de componentes
* **Defaults:** D-016 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/bom/**`, `frontend/src/features/inventory/bom/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-BOM-01 | P09 | unit | G2 | Cycle detection across multi-level BOMs; overlapping versions rejected |
| T-INV-BOM-02 | P09 | unit | G2 | Explosion with yields/scrap is exact in Decimal; costed explosion matches valuation |
| T-INV-BOM-03 | P10 | integration | G2 | Kit sale consumes components at cost (existing kit behaviour preserved) |
| T-INV-BOM-04 | P22c | e2e | G3 | UI: create recipe v1, activate, copy to v2 with new dates |

## 12. F-INV-XFORM — Motor de transformaciones (presentación, porcionado, manual)
*Transformation engine (presentation, portioning, manual).* Owner **P09** (M1); proven at **G2**. Audit rows: I6, I7, I8.

* **Contributors:** P05: posting; P07: approvals; P17: PDF + reports; P12: POS reservations respected
* **Supersedes:**
  * No transformation existed in the pack; this is new mandatory scope.
* **Data model:**
  * `TransformationOrder`(company, warehouse, number (sequence TRF), type ∈ {PRESENTATION (repack/unpack), PORTIONING, MANUAL, PRODUCTION (BOM)}, template_id or bom_id (nullable for MANUAL), date, status, reason, additional_costs[], tolerance %, approved_by, posted_entry)
  * `TransformationInput`(item, presentation, location, lot, qty, base_qty, unit_cost (at post))
  * `TransformationOutput`(item, presentation, location, lot (new or same), qty, base_qty, actual_weight (variable-weight items), cost_share %, is_byproduct, is_waste)
  * `PortioningTemplate`(company, input item, outputs[] with expected yield % and cost share %, waste %, tolerance %, rounding rule) · `RepackRule`(item, from presentation, to presentation) derived from presentation factors
  * Invariants: Σ output value + expensed abnormal waste = Σ input value + additional costs (exact, Decimal, rounding remainder to main output); base quantities: inputs consumed exactly; no stock created from nothing (outputs need ≥ 1 input unless type PRODUCTION with BOM consumption); same company and warehouse scope
* **States and transitions:**
  * DRAFT → (reserve inputs) RESERVED → PENDING_APPROVAL (if outside tolerance or MANUAL above threshold) → APPROVED → POSTED (all moves + valuation + entry in one transaction) → REVERSED
  * Any failure during post → full rollback, reservations kept
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/inventory/transformations` (+`:reserve`, `:submit`, `:approve`, `:post`, `:reverse`, `:simulate` → stock + cost + journal preview) · CRUD `/portioning-templates` · GET `/repack-rules`
* **Errors (stable codes):**
  * stock_insufficient
  * transformation_unbalanced_value
  * transformation_yield_out_of_tolerance
  * transformation_no_inputs
  * transformation_cross_scope
  * reservation_conflict
  * uom_precision_violation
* **Posting:**
  * Same inventory account: no entry for value-neutral repack within one valuation account; different item categories: Dr output inventory accounts / Cr input inventory accounts; abnormal waste: Dr waste expense / Cr inventory; additional costs: Dr inventory (outputs) / Cr cost clearing account
* **Security:**
  * Permissions inventory.<doc>.{read,create,approve,post,reverse}; warehouse-scoped row policy; cost fields confidential (hidden for sales/POS/portal roles)
  * MANUAL transformations require permission inventory.transformation.manual and approval above the company threshold (default COP 1,000,000)
* **UI:**
  * Inventario › Producción › Transformaciones (tabs Presentación · Porcionado · Manual · Producción): document with inputs/outputs grids, live balance check (value and base qty), tolerance indicator, simulate preview, approval, post, print, reverse; Plantillas de porcionado editor
* **AI / MCP:**
  * AI proposes portioning outputs from a template and actual weights; confirmation required
* **Import / export:**
  * Template import; transformation export
* **Reports:**
  * Rendimiento real vs esperado por plantilla; mermas normales/anormales; costo por porción
* **Defaults:** D-013, D-015, D-016, D-032 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3
* **Code locations (inside the owners' plan paths):** P09: `backend/inventory/transformations/**`, `frontend/src/features/inventory/transformations/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-XFORM-01 | P09 | integration | G2 | Repack 2 boxes x12 → 24 units (unpack) and back (pack): canonical stock unchanged, packaged quants updated, no entry within one account |
| T-INV-XFORM-02 | P09 | integration | G2 | Portioning direct (no template): 10 kg input → 3 outputs with actual weights + waste; values balance exactly; rounding to main output |
| T-INV-XFORM-03 | P09 | integration | G2 | Portioning with template: yield outside ±2 % requires approval; inside posts directly |
| T-INV-XFORM-04 | P09 | integration | G2 | Manual multi-input/multi-output transformation balances value and base quantities; outputs without inputs rejected |
| T-INV-XFORM-05 | P09 | integration | G2 | Production order from BOM v2 consumes components with scrap %; byproduct valued by cost share |
| T-INV-XFORM-06 | P09 | concurrency | G2 | Concurrent transformation and POS sale on the same lot: reservations prevent overconsumption; one fails cleanly |
| T-INV-XFORM-07 | P09 | integration | G2 | Ledger failure during post rolls back all stock moves (no partial stock or ledger) |
| T-INV-XFORM-08 | P09 | property | G2 | Random transformations: Σ inventory value + expensed waste invariant holds; no negative stock |
| T-INV-XFORM-09 | P22c | e2e | G3 | UI: one transformation of each type end-to-end incl. simulate, approve, post, print, reverse |

## 13. F-INV-REQ — Solicitudes internas de mercancía, consolidación, traslado automático y/o compra
*Internal goods requisitions, consolidation, automatic transfer and/or purchase.* Owner **P11** (M2); proven at **G2**. Audit rows: I9, I9.1, I9.2, I9.3, I9.4.

* **Contributors:** P09: availability, reservations and transfer documents via kernel.interfaces.Inventory (fake_ok); P07: approval workflow; P04: requester hierarchy (org/cost center); P19: AI proposals (no auto-confirm)
* **Supersedes:**
  * P11 `PurchaseRequisition` (from users, reorder, AI): generalized — every request starts as an InternalRequisition; procurement is only the remainder after internal sourcing. P09 reorder job now creates InternalRequisitions.
* **Data model:**
  * `InternalRequisition`(company, number (sequence SOL), requester, requesting branch/warehouse, cost center/business unit dims, needed_by, priority, status, approval_route)
  * `RequisitionLine`(item, presentation, qty, base_qty, needed_by, delivery warehouse, status ∈ {OPEN, RESERVED, IN_TRANSFER, TO_PROCURE, IN_PURCHASE, FULFILLED, CANCELLED}, fulfilled_qty, lineage[])
  * `ConsolidationBatch`(company, rule (D-031), lines[] each with source line ids + qty) · `SourcingDecision`(line, source ∈ {TRANSFER, PURCHASE, MIXED}, from_warehouse, qty_transfer, qty_purchase, reason, created_by ∈ {user, system-policy}, policy_ref)
  * Invariants: Σ (transfer + purchase + cancelled) ≤ requested per source line; lineage keeps every source line → transfer line / PR line / PO line; same company only for transfers
* **States and transitions:**
  * Requisition: DRAFT → SUBMITTED → APPROVED/REJECTED (P07, SoD) → IN_PROGRESS → FULFILLED | PARTIALLY_FULFILLED_CLOSED | CANCELLED
  * Line sourcing (system action under policy after approval): available in priority warehouses (D-012) → reserve + DRAFT transfer; remainder → TO_PROCURE → consolidation → DRAFT purchase requisition/PO only within D-011 limits, otherwise a buyer task
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/purchasing/internal-requisitions` (+`:submit`, `:approve`, `:reject`, `:cancel`) · POST `/api/v1/purchasing/consolidations:run` (dry_run=true|false) · GET `/consolidations/{id}` (lineage) · POST `/api/v1/purchasing/internal-requisitions/{id}:source` (simulate|apply) · transfers: P09 `/api/v1/inventory/transfers` (+`:ship`, `:receive`)
* **Errors (stable codes):**
  * requisition_not_approved
  * sourcing_overallocation
  * transfer_cross_company
  * auto_po_limit_exceeded (creates buyer task instead)
  * stock_insufficient
* **Posting:**
  * Transfers: P09 posting (in-transit location; Dr inventory in-transit / Cr inventory source; on receipt Dr destination / Cr in-transit) — no double counting: in-transit stock is reported once
* **Security:**
  * Requesters see own requisitions; approvers by org/cost-center scope; buyers see TO_PROCURE; system-policy actions are logged with policy id and never bypass approval of POs
* **UI:**
  * Compras › Solicitudes (tabs Mis solicitudes · Por aprobar · Consolidación · Abastecimiento): request form with availability hint, approval inbox, consolidation preview with lineage, sourcing simulation (transfer vs purchase split), links to transfers/POs
* **AI / MCP:**
  * AI proposes consolidation/sourcing; user confirms; no auto-confirm
* **Import / export:**
  * Requisition import (CSV) as DRAFT
* **Reports:**
  * Cumplimiento de solicitudes (lead time, % por traslado vs compra); trazabilidad solicitud → traslado/OC
* **Defaults:** D-011, D-012, D-031 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P09 via kernel.interfaces.Inventory (availability, reserve, create_transfer) → G2
* **Code locations (inside the owners' plan paths):** P11: `backend/purchasing/requisitions/**`, `frontend/src/features/purchasing/requisitions/**`; P09: `backend/inventory/transfers/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-INV-REQ-01 | P11 | integration | G2 | Request → approval (SoD) → sourcing: available stock reserved + DRAFT transfer from priority warehouse; remainder TO_PROCURE |
| T-INV-REQ-02 | P11 | integration | G2 | Consolidation of 3 requests keeps lineage; Σ consolidated = Σ sources; split fulfilment closes each source line correctly |
| T-INV-REQ-03 | P11 | integration | G2 | Auto-PO draft only within D-011 limit and with a valid vendor price; above limit creates a buyer task; PO still needs approval |
| T-INV-REQ-04 | P09 | integration | G2 | Transfer ship/receive through in-transit; stock counted once; cross-company rejected |
| T-INV-REQ-05 | P11 | concurrency | G2 | Two requisitions sourcing the same last units: reservations prevent double allocation |
| T-INV-REQ-06 | G2 | cross | G2 | End-to-end S-REQ scenario: request → partial transfer + PO → receipt → requisition FULFILLED |
| T-INV-REQ-07 | P22c | e2e | G3 | UI flow across the four tabs |

## 14. F-PUR-CYCLE — Ciclo completo de compras con cotizaciones
*Full purchase cycle with RFQ, quotations, comparison and recommendation.* Owner **P11** (M2); proven at **G2**. Audit rows: I10, I10.1, I10.2, I10.3, I10.4, I10.5, I10.6, I10.7.

* **Contributors:** P09: goods receipt into bins/presentations; P06: tax computation for normalization; P07: award and PO approvals; P15: documento soporte when applicable (existing); P16: payment (CE)
* **Supersedes:**
  * P11 `RFQ` (comparison table) and `:award`: now with VendorQuote entity, deadlines, attachments, normalized comparison, explainable recommendation and mandatory award approval.
* **Data model:**
  * `Rfq`(company, number, source requisition lines, invited vendors (≥1), deadline (D-010), currency, terms, status)
  * `VendorQuote`(rfq, vendor, received_at, channel ∈ {internal_entry, supplier_portal}, valid_until, currency + rate at quote date, lines (item, presentation/uom, qty offered, unit price, discount, taxes, freight allocation, lead time days, notes), attachments (virus-scanned), status ∈ {RECEIVED, LATE, WITHDRAWN, DISQUALIFIED})
  * `QuoteComparison`(rfq, normalized lines: price per base UoM in COP incl. non-recoverable taxes + freight, lead time, vendor rating, payment terms; scores per D-009; recommendation + explanation; created_at; immutable snapshot)
  * `Award`(rfq, lines→vendor, reason (required if not the recommendation), approval) → PO(s) per vendor
* **States and transitions:**
  * RFQ: DRAFT → SENT (portal notification/local mail) → CLOSED (deadline) → EVALUATED (comparison snapshot) → AWARD_PENDING_APPROVAL → AWARDED → PO_CREATED | CANCELLED
  * PO (existing): DRAFT → APPROVAL by amount → APPROVED → SENT/ACKNOWLEDGED (portal) → PARTIALLY_RECEIVED → RECEIVED → (vendor invoice, 3-way match) → CLOSED
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/purchasing/rfqs` (+`:send`, `:close`, `:reopen` (reason), `:evaluate`, `:award`, `:approve-award`) · CRUD `/rfqs/{id}/quotes` (+attachments) · GET `/rfqs/{id}/comparison` · existing `/orders`, `/receipts` (partial/full), `/vendor-invoices` (3-way match)
* **Errors (stable codes):**
  * rfq_deadline_passed
  * quote_late
  * quote_currency_rate_missing
  * award_reason_required
  * award_not_approved
  * receipt_over_tolerance
  * three_way_mismatch
* **Posting:**
  * Receipts and vendor invoices: existing P11/P09 posting (GRNI etc.)
* **Security:**
  * Buyers see all quotes of their company; vendors (portal) see only their own RFQ invitations/quotes; quote prices of other vendors never exposed; award approval SoD (buyer ≠ approver)
* **UI:**
  * Compras › Cotizaciones (tabs Solicitudes de cotización · Cotizaciones recibidas · Comparación): comparison matrix with normalized values and score breakdown, recommendation badge with explanation, award dialog with reason, approval; Compras › Órdenes (tabs Órdenes · Recepciones)
* **AI / MCP:**
  * AI summarizes the comparison and explains the recommendation; cannot award
* **Import / export:**
  * Quote import (CSV per vendor)
* **Reports:**
  * Ahorro vs recomendación, tiempos de respuesta de proveedores, OC por estado
* **Defaults:** D-009, D-010, D-011 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P09 via kernel.interfaces.Inventory (receive, quality hold) → G2; P15 via kernel.interfaces.FiscalDocuments (support document / vendor e-invoice status) → G2
* **Code locations (inside the owners' plan paths):** P11: `backend/purchasing/rfq/**`, `frontend/src/features/purchasing/rfq/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PUR-CYCLE-01 | P11 | integration | G2 | RFQ to 3 vendors, quotes received (one late), deadline closes; late quote excluded unless reopened |
| T-PUR-CYCLE-02 | P11 | unit | G2 | Normalization: UoM/presentation to base, FX at quote date, taxes and freight; golden comparison table |
| T-PUR-CYCLE-03 | P11 | unit | G2 | Scoring and tie rules deterministic and explainable (golden explanation text) |
| T-PUR-CYCLE-04 | P11 | integration | G2 | Award differing from recommendation requires a reason; award approval required before PO |
| T-PUR-CYCLE-05 | P11 | integration | G2 | PO partial receipt then full receipt; tolerance; 3-way match with vendor invoice |
| T-PUR-CYCLE-06 | P11 | integration | G2 | Quote attachments stored, scanned, downloadable by buyers only |
| T-PUR-CYCLE-07 | G2 | cross | G2 | S-PUR scenario requisition → RFQ → award → PO → 2 receipts → invoice → CE payment |
| T-PUR-CYCLE-08 | P22c | e2e | G3 | UI: comparison screen and award flow |

## 15. F-FORMULA-ENGINE — Motor seguro de fórmulas (DSL decimal)
*Safe Decimal formula engine (shared DSL).* Owner **P07** (M4); proven at **G1**.

* **Contributors:** P08: formula editor component (syntax highlight, validation, test cases, explain); P10: pricing; P13: payroll
* **Supersedes:**
  * P07 approval-condition DSL: generalized into one sandboxed engine used by approvals, pricing and payroll.
* **Data model:**
  * Grammar: numbers (Decimal literals), strings, booleans, identifiers from a declared input schema, + - * / % comparisons, and/or/not, if(cond, a, b), min, max, abs, round(x, n, mode), floor, ceil, clamp, lookup(table, key) over versioned tables, date functions (days_between, month, year), percent(x, p). No loops, no recursion, no attribute access, no indexing beyond lookup, no imports, no IO, no eval/exec/compile, no Python AST execution
  * Parser builds its own AST (hand-written or a parser library pinned in uv.lock); evaluator is a whitelisted tree walker with Decimal context (precision 28) and budgets (D-008)
  * `Formula`(company, domain ∈ {approval, pricing, payroll}, code, version, effective_from, effective_to, expression, input_schema, output type/precision, depends_on[] (other formula codes), test_cases[] (inputs → expected), status ∈ {DRAFT, ACTIVE, RETIRED}, author, approved_by, hash)
  * `FormulaEvaluation` snapshot stored with each priced line / payroll line: formula id+version+hash, inputs, intermediate values (explain trace), result
* **States and transitions:**
  * DRAFT → (validate: parse, type check, budgets, dependency DAG acyclic, all test cases pass) → PENDING_APPROVAL → ACTIVE (effective window) → RETIRED; ACTIVE versions immutable
* **API (P02 freezes the contract at G0):**
  * POST `/api/v1/workflow/formulas:validate` · `:simulate` (inputs → result + explain) · CRUD `/api/v1/workflow/formulas` (+`:submit`, `:approve`, `:retire`) · GET `/formulas/{id}/versions`
* **Errors (stable codes):**
  * formula_syntax_error (position)
  * formula_unknown_identifier
  * formula_type_error
  * formula_budget_exceeded
  * formula_dependency_cycle
  * formula_test_case_failed
  * formula_division_by_zero (explicit, never NaN)
* **Posting:**
  * The engine never posts: callers (pricing P10, payroll P13) store the explain snapshot inside the same transaction as their document; posted documents are never re-evaluated.
* **Security:**
  * Permission workflow.formula.{author,approve}; SoD author ≠ approver; formula text is data, never code; evaluation runs in-process with budgets (no subprocess, no network); audit of every version
* **UI:**
  * Shared editor (P08) used by Ventas › Fórmulas de precio and Talento › Conceptos y fórmulas: syntax highlighting, inline errors with position, input schema panel, test-case table, simulate with explain trace, version diff
* **AI / MCP:**
  * AI may draft a formula and test cases; it is saved as DRAFT and needs human approval
* **Import / export:**
  * Formula import/export JSON with tests
* **Reports:**
  * Formula catalogue report: formulas, versions, effective dates, dependents, last test-case run (pass/fail) and approval history; filter by owner module; CSV/XLSX export via P18.
* **Defaults:** D-008 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P08 via frontend FormulaEditor component contract (UI-CONTRACT, frozen at G0) → G1
* **Code locations (inside the owners' plan paths):** P07: `backend/workflow/formula/**`, `frontend/src/features/workflow/formula/**`; P08: `frontend/src/design-system/formula-editor/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-FORMULA-ENGINE-01 | P07 | unit | G1 | Golden evaluation suite (≥ 200 cases) incl. rounding modes and Decimal precision |
| T-FORMULA-ENGINE-02 | P07 | security | G1 | Adversarial corpus: __import__, attribute access, dunder names, lambda, comprehension, huge exponents, deep nesting, long strings, unicode tricks, recursion via dependencies → rejected; no Python eval/exec used (static scan) |
| T-FORMULA-ENGINE-03 | P07 | security | G1 | Budget exhaustion and timeouts produce formula_budget_exceeded; no CPU starvation of other requests |
| T-FORMULA-ENGINE-04 | P07 | unit | G1 | Dependency cycle detection; topological evaluation order |
| T-FORMULA-ENGINE-05 | P07 | security | G1 | Exfiltration attempts (lookup of tables outside the declared schema, cross-company tables) rejected |
| T-FORMULA-ENGINE-06 | P08 | unit | G1 | Editor shows positioned errors and runs test cases; accessible labels |

## 16. F-SAL-PRICING — Precios formulados de productos y servicios
*Formula-based pricing for products and services.* Owner **P10** (M1); proven at **G2**. Audit rows: V1.

* **Contributors:** P07: formula engine; P09: cost inputs (confidential); P06: taxes after price; P08: editor
* **Supersedes:**
  * P10 price lists/discounts: kept; formula pricing added with precedence D-024. Promotions [A] remain optional (not requested).
* **Data model:**
  * `PriceFormula`(company, scope: item/category/service/customer group/customer/currency/channel, formula ref (domain pricing), inputs: base_price, list_price, cost (permission-gated), qty, uom/presentation, customer attributes, date, currency rate; priority, effective dates, status)
  * Line price snapshot: formula id+version+hash, inputs, explain trace, result before tax, rounding rule, override (user, reason, approval)
* **States and transitions:**
  * Formula lifecycle per FORMULA-ENGINE; price resolution at line entry/recalc in DRAFT documents only
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/sales/price-formulas` · POST `/api/v1/sales/pricing:quote` (customer, items, qty, date, currency) → price + explain · document lines call the same resolver
* **Errors (stable codes):**
  * pricing_formula_missing_input
  * pricing_override_requires_approval
  * pricing_posted_document_immutable
  * formula_*
* **Posting:**
  * None directly; invoice posting unchanged; taxes computed by P06 on the resolved price
* **Security:**
  * Cost input visible only to roles with cost permission; portal users never see formulas or cost; explanation shown to customers is limited to final price
* **UI:**
  * Ventas › Clientes (tab Fórmulas de precio): list, editor (shared), test cases, simulate for a customer/item/qty; price explain popover on document lines
* **AI / MCP:**
  * AI explains a price with the trace; cannot change formulas without approval
* **Import / export:**
  * Price formula import/export
* **Reports:**
  * Margen por fórmula; overrides
* **Defaults:** D-024, D-008 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P10: `backend/sales/pricing/**`, `frontend/src/features/sales/pricing/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-SAL-PRICING-01 | P10 | integration | G2 | Precedence override > formula > list > base; qty breaks and customer group scopes |
| T-SAL-PRICING-02 | P10 | integration | G2 | Snapshot stored; reprice of a DRAFT shows a diff; posted invoice never repriced |
| T-SAL-PRICING-03 | P10 | integration | G2 | Override beyond discount limit routes to approval |
| T-SAL-PRICING-04 | P10 | security | G2 | Customer portal quote API never exposes cost or the formula text |
| T-SAL-PRICING-05 | P10 | unit | G2 | Multi-currency formula with FX at document date; rounding per currency |

## 17. F-PT-OPEN — Arquitectura abierta para proveedores tecnológicos
*Open architecture for DIAN technology providers.* Owner **P15** (M3); proven at **G2**. Audit rows: V2.

* **Contributors:** P04: integration credentials (vault/secret refs); P23: external gate EG-1
* **Supersedes:**
  * None (already FULL); acceptance made explicit.
* **Data model:**
  * `FiscalGateway` port with adapters `ProviderAdapter` (generic HTTP, per-PT mapping hooks) and simulated adapter (FIXTURE_NOT_CERTIFIED); `ProviderProfile`(name, base URL, auth type, mapping version, status ∈ {SIMULATED, SANDBOX_CONFIGURED, NOT_CONFIGURED})
* **States and transitions:**
  * Provider adapter configuration: draft → tested (sandbox/simulator conformance suite PASSED) → active → retired; exactly one active provider per company and document family; switching requires approval.
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/einvoice/providers` (secrets referenced, never returned) · POST `…/{id}:test-connection` (only SANDBOX with operator credentials)
* **Errors (stable codes):**
  * provider_not_configured
  * provider_contract_test_failed
* **Posting:**
  * N/A: transmission through a provider never changes the ledger; the invoice posts in P10 independently; status callbacks only update the fiscal-document status.
* **Security:**
  * Credentials only in the secrets store; masked in UI/logs
* **UI:**
  * Facturación › Numeración (tab Proveedores tecnológicos): profiles, status badge 'SIMULADO — no certificado'
* **AI / MCP:**
  * AI/MCP may read provider status and masked configuration and propose a switch as a draft requiring human approval; credentials are never readable by AI/MCP.
* **Import / export:**
  * Export of provider configuration without secrets and of the transmission log (CSV); import of provider config disabled (secrets are entered only via the secrets file/vault).
* **Reports:**
  * Transmission log by provider/status/date with rejection rate and latency; drill to the fiscal document.
* **External inputs (never invented; do not block DEV):**
  * Real PT contracts/credentials (EG-1)
* **Defaults:** D-020 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P15: `backend/einvoice/adapters/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PT-OPEN-01 | P15 | integration | G2 | Contract test suite runs against the simulated adapter and a second dummy adapter (proves pluggability) |
| T-PT-OPEN-02 | P15 | security | G2 | Provider secrets never appear in API responses, logs or exports |

## 18. F-PAY-FORMULA — Conceptos de nómina formulados (devengos y deducciones)
*Formula-based payroll concepts (earnings and deductions).* Owner **P13** (M4); proven at **G2**. Audit rows: N1, N1.1, N1.2, N1.3.

* **Contributors:** P07: formula engine; P06: legal parameter tables (fixture values in dev); P08: editor; P05: payroll posting
* **Supersedes:**
  * P13 formula catalog (code): kept for legal concepts; user-defined concepts are added through the shared DSL (domain payroll).
* **Data model:**
  * `PayrollConcept` (existing) + `formula_ref` (domain payroll), `depends_on[]` (concept codes), period_version, rounding, accounting role, DIAN e-payroll mapping, is_user_defined
  * Inputs schema: contract salary, days worked, hours by type, novelties, previous concepts' results, legal parameters (versioned table, fixture in dev, flagged NOT LEGALLY VERIFIED), employee attributes (restricted)
  * Payroll line snapshot: concept, formula version hash, inputs, explain trace, result
* **States and transitions:**
  * Concept formula DRAFT → validated (dependency DAG per period version) → approved (payroll admin ≠ author) → ACTIVE from a period
  * Payroll run: existing states; recalculation only before posting
* **API (P02 freezes the contract at G0):**
  * CRUD `/api/v1/payroll/concepts` (+formula) · POST `/api/v1/payroll/concepts:validate-graph` · POST `/api/v1/payroll/runs/{id}:simulate`
* **Errors (stable codes):**
  * formula_dependency_cycle
  * payroll_parameter_unknown (fail closed)
  * formula_*
* **Posting:**
  * Existing payroll posting by concept accounting role; dims from contract/cost center
* **Security:**
  * Salary inputs restricted; employee portal never shows formulas of other employees; simulate requires payroll permission
* **UI:**
  * Talento › Nómina (tab Conceptos y fórmulas): concept list, shared editor, dependency graph view, simulate for a synthetic employee, explain per payslip line
* **AI / MCP:**
  * AI explains a payslip line; cannot change concepts without approval
* **Import / export:**
  * Concept import/export
* **Reports:**
  * Nómina por concepto/centro de costo (existing)
* **External inputs (never invented; do not block DEV):**
  * Legal parameter values and golden cases (EG-7)
* **Defaults:** D-025, D-008 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P13: `backend/payroll/concepts/**`, `frontend/src/features/payroll/concepts/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PAY-FORMULA-01 | P13 | integration | G2 | User-defined earning and deduction concepts evaluated in topological order; cycle rejected on save |
| T-PAY-FORMULA-02 | P13 | integration | G2 | Parameter snapshot stored; changing parameters later does not change posted payrolls |
| T-PAY-FORMULA-03 | P13 | integration | G2 | Unknown legal parameter fails closed; fixture parameters labelled NOT LEGALLY VERIFIED in UI and payslip |
| T-PAY-FORMULA-04 | P13 | security | G2 | Adversarial formulas (same corpus as FORMULA-ENGINE) rejected in the payroll domain |
| T-PAY-FORMULA-05 | P22c | e2e | G3 | UI: create concept with formula, simulate, run payroll, see explain on payslip |

## 19. F-PORTAL-CORE — Base común de portales (identidad externa, invitaciones, shell)
*Portal foundation (external identity, invitations, portal shell).* Owner **P04** (M2); proven at **G1**.

* **Contributors:** P08: portal shell variant (same NexusERP look, limited menus, no admin, no AI panel by default); P07: notifications to local mail capture; P21: seeded portal accounts; P22d: portal login e2e
* **Supersedes:**
  * P04 External/delegated identities: extended with portal accounts; P08 shell: adds a portal variant.
* **Data model:**
  * `ExternalAccount`(id, tenant, company, kind ∈ {SUPPLIER, CUSTOMER, EMPLOYEE}, party_id or employee_id (exactly one), idp_subject, email, status ∈ {INVITED, ACTIVE, SUSPENDED, REVOKED}, invited_by, invited_at, last_login_at)
  * `PortalInvitation`(account, token hash, expires_at (72 h), used_at) · Keycloak client `erp-portal` (PKCE), groups per kind (D-018, D-033)
* **States and transitions:**
  * INVITED → ACTIVE (accept + set password in Keycloak) → SUSPENDED ↔ ACTIVE → REVOKED (terminal); invitation expiry 72 h; resend creates a new token
* **API (P02 freezes the contract at G0):**
  * Internal: POST `/api/v1/iam/external-accounts` (invite), `:suspend`, `:reactivate`, `:revoke`, `:resend` · Portal: GET `/api/portal/v1/me`, `/api/portal/v1/context`
* **Errors (stable codes):**
  * external_account_scope_violation
  * invitation_expired
  * external_role_forbidden
  * portal_kind_mismatch
* **Posting:**
  * N/A: portals never post directly; portal actions create drafts/requests that internal owners approve and post through their normal services.
* **Security:**
  * External user = exactly one party (or employee) in exactly one company of one tenant (D-018); every query filtered by the token's party/company claims in the repository layer (not only the API); no internal role can be granted to an external user; internal prices of other customers, costs, margins, formulas, other parties' documents and payroll data are never returned; rate limits per account; audit of every access; portal API under `/api/portal/v1/*` with separate OAuth client and scopes; AI/MCP not available to external users in dev
* **UI:**
  * Administración › Usuarios (tab Externos / portales): invite, status, last login; Portal shell: top bar (brand, company name, notifications, theme, profile), left menu limited to the portal kind, dashboard with 3–5 KPIs, DEV banner
* **AI / MCP:**
  * None for external users (internal AI may summarize portal activity)
* **Import / export:**
  * Bulk invitation import (CSV of parties) → INVITED
* **Reports:**
  * External access report: invitations (sent/accepted/expired), last login, active sessions, revocations, per portal kind; CSV export; audit drill.
* **Defaults:** D-018, D-033 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P08 via portal shell contract frozen at G0 → G1; P07 via kernel.interfaces.Notifications → G1
* **Code locations (inside the owners' plan paths):** P04: `backend/iam/external/**`, `frontend/src/features/iam/external/**`; P08: `frontend/src/shell/portal/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PORTAL-CORE-01 | P04 | integration | G1 | Invite → accept → login (local OIDC) → /me returns only the linked party/company; revoked account denied |
| T-PORTAL-CORE-02 | P04 | security | G1 | Token for party A cannot read party B data by changing ids in URL/body (IDOR suite across all portal endpoints) |
| T-PORTAL-CORE-03 | P04 | security | G1 | External user cannot be assigned internal roles; internal API rejects portal tokens |
| T-PORTAL-CORE-04 | P08 | unit | G1 | Portal shell renders only the menus of its kind; no admin/AI panel; tokens identical to internal shell |
| T-PORTAL-CORE-05 | P22d | e2e | G4 | Browser login for the 3 portal kinds and the internal app with seeded accounts (credentials read from the secrets file at runtime, never printed) |

## 20. F-PORTAL-SUP — Portal de autoservicio de proveedores
*Supplier self-service portal.* Owner **P11** (M2); proven at **G2**. Audit rows: P1.

* **Contributors:** P04: external identity; P08: portal shell; P09: shipment notices to receiving (fake_ok); P15: documento soporte status (fake_ok); P17: PDF downloads
* **Supersedes:**
  * shared/06 'vendor portal [A]': mandatory.
* **Data model:**
  * `SupplierProfileChange`(party, fields (bank account, contacts, tax data), documents (RUT, certifications), status ∈ {SUBMITTED, APPROVED, REJECTED}) — internal approval before any master data changes
  * `ShipmentNotice`(PO, lines, dates, tracking text) → visible to receiving · `PoAcknowledgement`(PO, accepted/changes requested)
* **States and transitions:**
  * Profile change SUBMITTED → APPROVED/REJECTED (internal, SoD; bank changes need second approver)
  * RFQ invitation → quote submitted (before deadline) / withdrawn
  * PO SENT → ACKNOWLEDGED | CHANGE_REQUESTED
* **API (P02 freezes the contract at G0):**
  * `/api/portal/v1/supplier/profile` (GET, change requests POST) · `/documents` · `/rfqs` (own invitations) + `/rfqs/{id}/quotes` · `/purchase-orders` (+`:acknowledge`) · `/shipments` · `/invoices` (status only; upload of electronic invoice file/XML for P11 ingestion) · `/payments` (status of own payables and CEs)
* **Errors (stable codes):**
  * portal_rfq_closed
  * portal_profile_change_pending
  * external_account_scope_violation
* **Posting:**
  * None: the portal never posts; uploads create DRAFT items for internal users
* **Security:**
  * External user = exactly one party (or employee) in exactly one company of one tenant (D-018); every query filtered by the token's party/company claims in the repository layer (not only the API); no internal role can be granted to an external user; internal prices of other customers, costs, margins, formulas, other parties' documents and payroll data are never returned; rate limits per account; audit of every access; portal API under `/api/portal/v1/*` with separate OAuth client and scopes; AI/MCP not available to external users in dev
* **UI:**
  * Portal de proveedores: Inicio (KPIs), Cotizaciones, Órdenes, Envíos, Facturas, Pagos, Perfil y documentos
* **AI / MCP:**
  * None
* **Import / export:**
  * None (portal)
* **Reports:**
  * Supplier statement (POs, receipts, invoices, payments, withholding certificates) as PDF/CSV, scoped to the supplier party only.
* **Portal dashboard KPIs (also in `ui/module-registry.json › portals`):**
  * `sp_rfq_abiertas` Solicitudes de cotización abiertas = count(RFQ invitations of my party with status SENT and deadline ≥ today) (count, snapshot)
  * `sp_oc_por_confirmar` Órdenes por confirmar = count(POs of my party with status SENT not acknowledged) (count, snapshot)
  * `sp_facturas_por_pagar` Facturas por pagar = Σ open AP items of my party (COP) (COP, snapshot)
  * `sp_pagos_periodo` Pagos recibidos en el periodo = Σ posted CE amounts to my party in the selected period (COP, flow)
* **Defaults:** D-018, D-033 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P09 via kernel.interfaces.Inventory (shipment notice → receiving) → G2; P15 via kernel.interfaces.FiscalDocuments (status) → G2; P17 via kernel.interfaces.DocumentRenderer (PDF) → G3; P16 via kernel.interfaces.PaymentStatus (CE status) → G2
* **Code locations (inside the owners' plan paths):** P11: `backend/purchasing/portal/**`, `frontend/src/features/purchasing/portal/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PORTAL-SUP-01 | P11 | integration | G2 | Supplier submits a quote before deadline via portal; buyer sees it in the comparison; another supplier cannot see it |
| T-PORTAL-SUP-02 | P11 | integration | G2 | PO acknowledgement and shipment notice flow; receiving sees the notice |
| T-PORTAL-SUP-03 | P11 | integration | G2 | Bank account change request requires two internal approvals before the vendor master changes |
| T-PORTAL-SUP-04 | P11 | security | G2 | Portal responses never include internal prices of other vendors, costs or internal notes |
| T-PORTAL-SUP-05 | P11 | integration | G2 | KPIs computed from synthetic data match direct queries |
| T-PORTAL-SUP-06 | P22d | e2e | G4 | Supplier portal: login, dashboard KPIs, quote submission, PO acknowledgement, payment status; visual + a11y |

## 21. F-PORTAL-CUS — Portal de autoservicio de clientes
*Customer self-service portal.* Owner **P10** (M1); proven at **G2**. Audit rows: P2.

* **Contributors:** P04: external identity; P08: portal shell; P15: fiscal documents (synthetic, labelled); P16: receipts (RC) status; P17: PDF downloads
* **Supersedes:**
  * P10 customer 360 (internal only): a customer portal is added; tickets [A] remain optional.
* **Data model:**
  * `PortalQuoteRequest`(party, items, qty, notes) → internal quote DRAFT · `PaymentIntent`(party, invoices, amount, simulated provider ref, status ∈ {CREATED, SIMULATED_SUCCEEDED, SIMULATED_FAILED, EXPIRED}) — never posts (D-021)
  * `CustomerProfileChange`(contacts, addresses, tax data) → internal approval
* **States and transitions:**
  * Quote request SUBMITTED → QUOTED (internal) → ACCEPTED (customer) → order
  * PaymentIntent CREATED → SIMULATED_SUCCEEDED → treasury creates RC DRAFT (manual post)
* **API (P02 freezes the contract at G0):**
  * `/api/portal/v1/customer/quotes` · `/orders` · `/invoices` (+`/pdf`, `/xml` labelled SINTÉTICO in dev) · `/credit-notes` · `/receipts` · `/statement?from=&to=` · `/payment-intents` · `/requests` · `/profile`
* **Errors (stable codes):**
  * portal_document_not_owned
  * payment_intent_amount_invalid
  * external_account_scope_violation
* **Posting:**
  * None from the portal; RC posting stays internal
* **Security:**
  * External user = exactly one party (or employee) in exactly one company of one tenant (D-018); every query filtered by the token's party/company claims in the repository layer (not only the API); no internal role can be granted to an external user; internal prices of other customers, costs, margins, formulas, other parties' documents and payroll data are never returned; rate limits per account; audit of every access; portal API under `/api/portal/v1/*` with separate OAuth client and scopes; AI/MCP not available to external users in dev
* **UI:**
  * Portal de clientes: Inicio (KPIs), Cotizaciones, Pedidos, Facturas y notas, Pagos y recibos, Estado de cuenta, Solicitudes, Perfil
* **AI / MCP:**
  * None
* **Import / export:**
  * Statement export PDF/CSV
* **Reports:**
  * Customer account statement (invoices, notes, receipts, balance aging) as PDF/CSV, scoped to the customer party only.
* **Portal dashboard KPIs (also in `ui/module-registry.json › portals`):**
  * `cp_saldo_pendiente` Saldo pendiente = Σ open AR items of my party (COP) (COP, snapshot)
  * `cp_facturas_vencidas` Facturas vencidas = count(open AR items of my party with due_date < today) (count, snapshot)
  * `cp_pedidos_en_curso` Pedidos en curso = count(sales orders of my party not fully delivered) (count, snapshot)
  * `cp_pagos_periodo` Pagos del periodo = Σ posted RC applications to my party in the selected period (COP, flow)
* **Defaults:** D-018, D-021, D-033 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P15 via kernel.interfaces.FiscalDocuments → G2; P16 via kernel.interfaces.PaymentStatus (RC status) → G2; P17 via kernel.interfaces.DocumentRenderer (PDF) → G3
* **Code locations (inside the owners' plan paths):** P10: `backend/sales/portal/**`, `frontend/src/features/sales/portal/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PORTAL-CUS-01 | P10 | integration | G2 | Customer sees only own invoices/credit notes/receipts; statement totals equal AR subledger |
| T-PORTAL-CUS-02 | P10 | integration | G2 | Payment intent simulated success never posts; RC draft appears for treasury |
| T-PORTAL-CUS-03 | P10 | integration | G2 | Quote request → internal quote → acceptance → sales order |
| T-PORTAL-CUS-04 | P10 | security | G2 | No internal prices of other customers, cost, margin or price formula in any portal response |
| T-PORTAL-CUS-05 | P10 | integration | G2 | Downloaded fiscal PDFs/XMLs in dev carry the 'SINTÉTICO — sin validez fiscal' label |
| T-PORTAL-CUS-06 | P22d | e2e | G4 | Customer portal: login, KPIs, invoice download, statement, payment intent; visual + a11y |

## 22. F-PORTAL-EMP — Portal de autoservicio de empleados
*Employee self-service portal.* Owner **P13** (M4); proven at **G2**. Audit rows: P3.

* **Contributors:** P04: external identity (employee kind); P08: portal shell; P17: certificates PDF
* **Supersedes:**
  * P13 self-service portal: kept and given explicit acceptance, KPIs and tests.
* **Data model:**
  * Existing P13 self-service (payslips, certificates, vacation requests, data updates) exposed under `/api/portal/v1/employee/*`; `DataUpdateRequest` with internal approval
* **States and transitions:**
  * Vacation request SUBMITTED → APPROVED/REJECTED (manager) → TAKEN; data update SUBMITTED → APPROVED/REJECTED (HR)
* **API (P02 freezes the contract at G0):**
  * `/api/portal/v1/employee/payslips` (+pdf) · `/certificates` (laboral, ingresos y retenciones — fixture-labelled) · `/vacations` · `/data-updates` · `/profile`
* **Errors (stable codes):**
  * portal_document_not_owned
  * vacation_balance_insufficient
* **Posting:**
  * N/A: the employee portal never posts; vacation requests and data-update requests go to HR approval in P13.
* **Security:**
  * External user = exactly one party (or employee) in exactly one company of one tenant (D-018); every query filtered by the token's party/company claims in the repository layer (not only the API); no internal role can be granted to an external user; internal prices of other customers, costs, margins, formulas, other parties' documents and payroll data are never returned; rate limits per account; audit of every access; portal API under `/api/portal/v1/*` with separate OAuth client and scopes; AI/MCP not available to external users in dev
  * Only own payroll data; salary fields visible only to the employee themself and payroll roles
* **UI:**
  * Portal de empleados: Inicio (KPIs), Desprendibles, Certificados, Vacaciones, Actualización de datos, Perfil
* **AI / MCP:**
  * None
* **Import / export:**
  * None
* **Reports:**
  * Employee downloads only: payslips (desprendibles), labour certificate, income/withholding certificate (certificado de ingresos y retenciones, non-certified dev format), scoped to the employee.
* **Portal dashboard KPIs (also in `ui/module-registry.json › portals`):**
  * `ep_dias_vacaciones` Días de vacaciones disponibles = accrued vacation days − taken − approved pending (own) (days, snapshot)
  * `ep_ultimo_pago` Último pago neto = net pay of my last posted payslip (COP, snapshot)
  * `ep_solicitudes_pendientes` Solicitudes pendientes = count(my vacation + data update requests in SUBMITTED) (count, snapshot)
* **Defaults:** D-018, D-033 (DECISIONS-DEFAULTS.md)
* **Contract stubs (frozen at G0; real wiring proven at the gate):** P17 via kernel.interfaces.DocumentRenderer (PDF) → G3
* **Code locations (inside the owners' plan paths):** P13: `backend/hr/portal/**`, `frontend/src/features/hr/portal/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-PORTAL-EMP-01 | P13 | integration | G2 | Employee sees only own payslips/certificates; certificate PDF labelled with fixture parameter status |
| T-PORTAL-EMP-02 | P13 | integration | G2 | Vacation request approval flow updates balance KPI |
| T-PORTAL-EMP-03 | P13 | security | G2 | Employee A cannot access employee B payslips (IDOR) nor any salary aggregate |
| T-PORTAL-EMP-04 | P22d | e2e | G4 | Employee portal: login, KPIs, payslip download, vacation request; visual + a11y |

## 23. F-X-AI-MCP — IA y MCP sobre todas las funciones nuevas
*AI agent and MCP coverage of the new features.* Owner **P19** (M2); proven at **G3**.

* **Contributors:** P20: MCP tools mirroring the APIs with OAuth scopes
* **Data model:**
  * Tool catalog entries per feature (read/search + proposal tools); proposals create DRAFT objects or pending actions that require explicit user confirmation in the product UI; delegated user identity; same row/field policies
* **API (P02 freezes the contract at G0):**
  * Agent tools: dimensions, branches, treasury vouchers (draft), issues (draft), transformations (simulate/draft), requisitions (draft/sourcing simulate), RFQ comparison explain, pricing explain, payroll explain, exógena validation explain
  * MCP: read/search counterparts (scope `<module>.read`)
* **Errors (stable codes):**
  * ai_action_requires_confirmation
  * mcp_scope_insufficient
* **Security:**
  * No auto-confirm, no approvals by AI, no portal access to AI in dev; fixture adapter visibly labelled (D-020)
* **UI:**
  * AI panel shows 'IA en modo de prueba' when the fixture adapter is active; proposals render as confirm cards
* **Defaults:** D-020 (DECISIONS-DEFAULTS.md)
* **Code locations (inside the owners' plan paths):** P19: `backend/ai_agent/tools/**`; P20: `mcp_server/tools/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-AI-MCP-01 | P19 | security | G3 | Every new proposal tool creates only DRAFT/pending items; confirmation by the user is required; AI cannot approve or post |
| T-X-AI-MCP-02 | P19 | security | G3 | AI tool results respect row/field policy (subtree grants, cost fields) — leak suite |
| T-X-AI-MCP-03 | P20 | security | G3 | MCP tools for new features deny anonymous and insufficient scopes |
| T-X-AI-MCP-04 | P19 | integration | G3 | Fixture adapter label visible; no canned answer presented as a real model |

## 24. F-X-DATAIO — Importación/exportación de los nuevos maestros y documentos
*Import/export for the new masters and documents.* Owner **P18** (M3); proven at **G3**.

* **Data model:**
  * Templates: dimensions (4), branches, treasury concepts, issue concepts, presentations, locations, barcodes (import GTIN), BOMs, portioning templates, price formulas, payroll concepts, exógena packs/mappings, external account invitations, requisitions (DRAFT)
* **API (P02 freezes the contract at G0):**
  * `/api/v1/dataio/templates` lists them; import is two-phase (validate → apply), atomic per batch, idempotent by batch hash
* **Errors (stable codes):**
  * import_validation_failed (row errors)
  * import_duplicate_batch
* **Security:**
  * Same permissions as the target module; cost columns only with cost permission
* **UI:**
  * Datos › Plantillas lists every template with example file and field help
* **Code locations (inside the owners' plan paths):** P18: `backend/dataio/templates/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-DATAIO-01 | P18 | integration | G3 | Every template: valid file applies, invalid file applies zero rows with row-level errors, export→import round trip is a no-op |
| T-X-DATAIO-02 | P18 | security | G3 | Import cannot create cross-company references or bypass approvals (documents import as DRAFT) |

## 25. F-X-SEED — Datos sintéticos para todas las funciones y KPIs
*Synthetic datasets for every feature and KPI.* Owner **P21** (M4); proven at **G3**.

* **Data model:**
  * demo_tenant.yaml extended: 2 companies, 2 branches each, 4 dimension trees, bins, presentations, barcodes (internal + restricted-circulation EAN), BOM/recipes, portioning templates, issue concepts, treasury concepts, RC/CE history, requisitions/RFQs/quotes/POs, price formulas, payroll formula concepts, exógena synthetic pack + mappings, 3 portal accounts per kind, approver accounts (D-023)
* **Security:**
  * Synthetic names marked SINTÉTICA; no real persons/NIT; passwords generated at seed time into the secrets file
* **UI:**
  * Every dashboard KPI and portal KPI shows non-empty values from posted synthetic transactions
* **Code locations (inside the owners' plan paths):** P21: `backend/onboarding/seed/**`, `backend/kernel/fixtures/demo_tenant.yaml`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-SEED-01 | P21 | integration | G3 | Seed is idempotent and produces non-zero values for every KPI in ui/module-registry.json and portals |
| T-X-SEED-02 | P21 | security | G3 | Seed contains no real-looking personal data patterns (scan) and writes no password to logs |

## 26. F-X-QA — Escenarios transversales, seguridad, E2E, visual y accesibilidad
*Cross-module scenarios, security, E2E, visual and accessibility.* Owner **P22d** (M5); proven at **G4**.

* **Contributors:** P22a: harness + fixtures; P22b: cross-module scenarios S-REQ, S-PUR, S-TRE, S-INV; P22c: leak/IDOR suites for portals and new APIs; feature E2E; P22d: visual/a11y + portal logins
* **Data model:**
  * Traceability `docs/traceability.csv` gains one row per feature test id (T-*) with status and evidence path; gate files list feature checks
* **Security:**
  * Leak tests cover all new endpoints, exports, reports, AI, MCP and portals
* **UI:**
  * Visual baselines for every new screen and the 3 portals (D-022); a11y scan with axe (no serious/critical issues except documented exceptions)
* **Code locations (inside the owners' plan paths):** P22d: `tests/e2e/**`, `tests/security/**`, `tests/cross_module/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-QA-01 | G2 | cross | G2 | S-TRE: invoice → RC partial + withholding → bank reconciliation by voucher id; vendor invoice → CE |
| T-X-QA-02 | G2 | cross | G2 | S-INV: receipt by presentation into bin → transformation (portioning) → issue to cost center → kardex and GL agree |
| T-X-QA-03 | P22c | security | G3 | Leak/IDOR suite over all portal endpoints and new internal endpoints |
| T-X-QA-04 | P22d | visual | G4 | Visual baselines for all new screens and portals approved once; no auto-regeneration |
| T-X-QA-05 | P22d | a11y | G4 | axe scan of all new screens and portals: zero serious/critical (documented exceptions only) |
| T-X-QA-06 | P02 | integration | G0 | Frozen OpenAPI contains every endpoint, event and stable error code listed in FEATURES-ADDENDUM (contract diff test against features/feature-registry.json) |
| T-X-QA-07 | G0 | doc | G0 | CCR-FEAT-001 recorded in the G0 attempt; every kernel.interfaces stub named in FEATURES-ADDENDUM exists as a typed Protocol with a test double |

## 27. F-X-COMPLIANCE — Etiquetas de no certificación y entradas legales externas
*Non-certification labels and external legal inputs.* Owner **P23** (M4); proven at **G4**.

* **Data model:**
  * Compliance packet lists: exógena official packs (B-14), PT credentials (EG-1), payroll legal parameters (EG-7), bank formats (EG-5) as external inputs, each with the in-product label shown in dev
* **UI:**
  * Labels: 'SINTÉTICO — no válido ante la DIAN', 'SIMULADO — no certificado', 'Parámetros sin verificación legal', 'IA en modo de prueba'
* **Code locations (inside the owners' plan paths):** P23: `docs/compliance/**`

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-COMPLIANCE-01 | P23 | doc | G4 | Packet lists every external input with its label; no statement of legal validity |
| T-X-COMPLIANCE-02 | G4 | e2e | G4 | Every label is visible on the corresponding screens/documents in the running dev build |

## 28. F-X-RUNTIME — Evidencia de ejecución en DEV de todas las funciones
*DEV runtime evidence for all features.* Owner **DEV** (M5); proven at **DEV**.

* **Data model:**
  * DEV evidence v2: feature_traceability (all check ids PASSED with report digests), portal_login_{supplier,customer,employee}, internal browser login, print_pdf_smoke, visual_regression, a11y, noncert_labels_visible
* **Security:**
  * Credentials read from the secrets file at runtime; never in evidence

| Test | Task | Level | Gate | Acceptance |
|---|---|---|---|---|
| T-X-RUNTIME-01 | DEV | runtime | DEV | Browser login (Playwright, mandatory) internal + 3 portals against the running stack at the selected local URL |
| T-X-RUNTIME-02 | DEV | runtime | DEV | Print smoke: RC, CE, label and portal invoice PDFs generated by the running stack and hashed |
| T-X-RUNTIME-03 | DEV | runtime | DEV | Visual + a11y suite against the running stack; artifacts on M5 |

## Audit mapping (all 25 parent items and 62 subchecks of the v2.1.1 audit)

| Audit row | Spanish | v2.1.1 | 2.2.0-dev | Mandatory tests | Gate |
|---|---|---|---|---|---|
| A1 | Archivos de exógena de Colombia | EXTERNAL-PARAMETER-BLOCKED | FULL (mandatory spec; engine + synthetic fixture; official legal data EXTERNAL, does not block DEV) | T-ACC-EXO-01, T-ACC-EXO-02, T-ACC-EXO-03, T-ACC-EXO-04, T-ACC-EXO-05, T-ACC-EXO-06 | G4 |
| A1.1 | Generador de formatos | FULL | FULL (mandatory spec) | T-ACC-EXO-02, T-ACC-EXO-03, T-ACC-EXO-04, T-ACC-EXO-05 | G3 |
| A1.2 | Formatos anuales DIAN y validaciones | EXTERNAL-PARAMETER-BLOCKED | FULL (mandatory spec; engine + synthetic fixture; official legal data EXTERNAL, does not block DEV) | T-ACC-EXO-01, T-ACC-EXO-06 | G4 |
| A2 | Centros de Costo (código, descripción, Agrupador/Detalle, vista tabla y árbol) | PARTIAL | FULL (mandatory spec) | T-ACC-DIM-01, T-ACC-DIM-02, T-ACC-DIM-03, T-ACC-DIM-04, T-ACC-DIM-05, T-ACC-DIM-06, T-ACC-DIM-07, T-ACC-DIM-08, T-ACC-DIM-09, T-ACC-DIM-10, T-ACC-DIM-11, T-ACC-DIM-12 | G3 |
| A2.1 | Entidad y jerarquía | FULL | FULL (mandatory spec) | T-ACC-DIM-01, T-ACC-DIM-03, T-ACC-DIM-09 | G1 |
| A2.2 | Dimensión en línea contable / requerida por cuenta | FULL | FULL (mandatory spec) | T-ACC-DIM-02, T-ACC-DIM-06, T-ACC-DIM-07 | G1 |
| A2.3 | Código y descripción | PARTIAL | FULL (mandatory spec) | T-ACC-DIM-01 | G1 |
| A2.4 | Agrupador/Detalle (solo Detalle imputable) | MISSING | FULL (mandatory spec) | T-ACC-DIM-02, T-ACC-DIM-03 | G1 |
| A2.5 | Vista tabla y árbol | MISSING | FULL (mandatory spec) | T-ACC-DIM-08, T-ACC-DIM-12 | G1 |
| A3 | Sucursales por empresa | PARTIAL | FULL (mandatory spec) | T-ACC-BRANCH-01, T-ACC-BRANCH-02, T-ACC-BRANCH-03, T-ACC-BRANCH-04, T-ACC-BRANCH-05, T-ACC-BRANCH-06 | G3 |
| A3.1 | Entidad sucursal y uso | FULL | FULL (mandatory spec) | T-ACC-BRANCH-01, T-ACC-BRANCH-03, T-ACC-BRANCH-04 | G2 |
| A3.2 | Pertenencia a empresa (FK) | PARTIAL | FULL (mandatory spec) | T-ACC-BRANCH-01, T-ACC-BRANCH-02 | G1 |
| A3.3 | Pantalla de mantenimiento | PARTIAL | FULL (mandatory spec) | T-ACC-BRANCH-01, T-ACC-BRANCH-06 | G3 |
| A4 | Unidades de negocio (código, descripción, Agrupador/Detalle, vista tabla y árbol) | MISSING | FULL (mandatory spec) | T-ACC-DIM-01, T-ACC-DIM-02, T-ACC-DIM-03, T-ACC-DIM-04, T-ACC-DIM-08, T-ACC-DIM-11, T-ACC-DIM-12 | G3 |
| A5a | Dimensión maestra: Auxiliar Abierto (código, descripción, Agrupador/Detalle, tabla y árbol) | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-01, T-ACC-DIM-02, T-ACC-DIM-03, T-ACC-DIM-04, T-ACC-DIM-05, T-ACC-DIM-06, T-ACC-DIM-07, T-ACC-DIM-08, T-ACC-DIM-09, T-ACC-DIM-10, T-ACC-DIM-11, T-ACC-DIM-12 | G3 |
| A5b | Dimensión maestra: Ítem (código, descripción, Agrupador/Detalle, tabla y árbol) | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-01, T-ACC-DIM-02, T-ACC-DIM-03, T-ACC-DIM-04, T-ACC-DIM-05, T-ACC-DIM-06, T-ACC-DIM-07, T-ACC-DIM-08, T-ACC-DIM-09, T-ACC-DIM-10, T-ACC-DIM-11, T-ACC-DIM-12 | G3 |
| A5a.1 | Auxiliar abierto: Código y descripción obligatorios; único por empresa | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-01 | G1 |
| A5a.2 | Auxiliar abierto: Agrupador/Detalle; imputación solo en Detalle | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-02 | G1 |
| A5a.3 | Auxiliar abierto: Árbol acíclico, profundidad configurable, re-parentado seguro | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-03, T-ACC-DIM-10 | G3 |
| A5a.4 | Auxiliar abierto: Alcance por empresa; imposible asignar entre empresas | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-04 | G1 |
| A5a.5 | Auxiliar abierto: Integración contable: slot en línea, reglas requerido/permitido en la misma transacción | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-06, T-ACC-DIM-07 | G1 |
| A5a.6 | Auxiliar abierto: Vista tabla y árbol (UI) | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-08, T-ACC-DIM-12 | G1 |
| A5a.7 | Auxiliar abierto: Seguridad, import/export, API, IA/MCP, reportes | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-09, T-ACC-DIM-11, T-X-AI-MCP-02, T-X-AI-MCP-03 | G3 |
| A5b.1 | Ítem contable: Código y descripción obligatorios; único por empresa | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-01 | G1 |
| A5b.2 | Ítem contable: Agrupador/Detalle; imputación solo en Detalle | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-02 | G1 |
| A5b.3 | Ítem contable: Árbol acíclico, profundidad configurable, re-parentado seguro | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-03, T-ACC-DIM-10 | G3 |
| A5b.4 | Ítem contable: Alcance por empresa; imposible asignar entre empresas | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-04 | G1 |
| A5b.5 | Ítem contable: Integración contable: slot en línea, reglas requerido/permitido en la misma transacción | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-06, T-ACC-DIM-07 | G1 |
| A5b.6 | Ítem contable: Vista tabla y árbol (UI) | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-08, T-ACC-DIM-12 | G1 |
| A5b.7 | Ítem contable: Seguridad, import/export, API, IA/MCP, reportes | FULL (spec, not built) | FULL (mandatory spec) | T-ACC-DIM-09, T-ACC-DIM-11, T-X-AI-MCP-02, T-X-AI-MCP-03 | G3 |
| A6 | Balance General, Estados Financieros, Estado de Resultados, Flujo de Caja, Auxiliar por cuenta, por centro de costo | PARTIAL | FULL (mandatory spec) | T-ACC-REPORTS-01, T-ACC-REPORTS-02, T-ACC-REPORTS-03, T-ACC-REPORTS-04, T-ACC-REPORTS-05, T-ACC-REPORTS-06, T-ACC-REPORTS-07 | G3 |
| A6.1 | Balance General | FULL | FULL (mandatory spec) | T-ACC-REPORTS-01, T-ACC-REPORTS-07 | G3 |
| A6.2 | Estados Financieros | FULL | FULL (mandatory spec) | T-ACC-REPORTS-04, T-ACC-REPORTS-07 | G3 |
| A6.3 | Estado de Resultados | FULL | FULL (mandatory spec) | T-ACC-REPORTS-02, T-ACC-REPORTS-07 | G3 |
| A6.4 | Flujo de Caja | FULL | FULL (mandatory spec) | T-ACC-REPORTS-03, T-ACC-REPORTS-07 | G3 |
| A6.5 | Auxiliar por cuenta | FULL | FULL (mandatory spec) | T-ACC-REPORTS-05, T-ACC-REPORTS-07 | G3 |
| A6.6 | Auxiliar por centro de costo | PARTIAL | FULL (mandatory spec) | T-ACC-REPORTS-06, T-ACC-REPORTS-07 | G3 |
| T1 | Recibos de caja | PARTIAL | FULL (mandatory spec) | T-TRE-RC-01, T-TRE-RC-02, T-TRE-RC-03, T-TRE-RC-04, T-TRE-RC-05, T-TRE-RC-06, T-TRE-RC-07, T-TRE-RC-08 | G3 |
| T1.1 | Recibo de cartera (CxC) | FULL | FULL (mandatory spec) | T-TRE-RC-01, T-TRE-RC-03, T-TRE-RC-04 | G2 |
| T1.2 | Recibo por concepto (no cartera) | MISSING | FULL (mandatory spec) | T-TRE-RC-02 | G2 |
| T1.3 | Consecutivo y formato impreso | MISSING | FULL (mandatory spec) | T-TRE-RC-03, T-TRE-RC-07, T-TRE-RC-08 | G3 |
| T2 | Comprobantes de egreso | PARTIAL | FULL (mandatory spec) | T-TRE-CE-01, T-TRE-CE-02, T-TRE-CE-03, T-TRE-CE-04, T-TRE-CE-05, T-TRE-CE-06, T-TRE-CE-07 | G3 |
| T2.1 | Pago a proveedor / lotes | FULL | FULL (mandatory spec) | T-TRE-CE-01, T-TRE-CE-03, T-TRE-CE-04 | G2 |
| T2.2 | Egreso directo por concepto | MISSING | FULL (mandatory spec) | T-TRE-CE-02 | G2 |
| T2.3 | Comprobante impreso | MISSING | FULL (mandatory spec) | T-TRE-CE-06, T-TRE-CE-07 | G3 |
| I1 | Salidas de inventario por tipo (consumo, consumo directo, bajas, otros conceptos) | PARTIAL | FULL (mandatory spec) | T-INV-ISSUE-01, T-INV-ISSUE-02, T-INV-ISSUE-03, T-INV-ISSUE-04, T-INV-ISSUE-05 | G3 |
| I1.1 | Bajas | FULL | FULL (mandatory spec) | T-INV-ISSUE-01, T-INV-ISSUE-04 | G2 |
| I1.2 | Por consumo (a centro de costo) | PARTIAL | FULL (mandatory spec) | T-INV-ISSUE-01, T-INV-ISSUE-02 | G2 |
| I1.3 | Consumo directo | MISSING | FULL (mandatory spec) | T-INV-ISSUE-01 | G2 |
| I1.4 | Otros conceptos configurables | MISSING | FULL (mandatory spec) | T-INV-ISSUE-01, T-INV-ISSUE-02, T-INV-ISSUE-05 | G3 |
| I2 | Presentaciones de producto y stock por presentación | MISSING | FULL (mandatory spec) | T-INV-PRES-01, T-INV-PRES-02, T-INV-PRES-03, T-INV-PRES-04, T-INV-PRES-05 | G3 |
| I3 | Ubicaciones dentro de bodegas, stock por ubicación | PARTIAL | FULL (mandatory spec) | T-INV-LOC-01, T-INV-LOC-02, T-INV-LOC-03, T-INV-LOC-04, T-INV-LOC-05 | G3 |
| I3.1 | Entidad ubicación y stock por ubicación | FULL | FULL (mandatory spec) | T-INV-LOC-01, T-INV-LOC-02 | G2 |
| I3.2 | Árbol dentro de la bodega | MISSING | FULL (mandatory spec) | T-INV-LOC-01 | G2 |
| I3.3 | Disponibilidad por ubicación | MISSING | FULL (mandatory spec) | T-INV-LOC-02, T-INV-LOC-04 | G2 |
| I4 | Generación e impresión de código de barras | PARTIAL | FULL (mandatory spec) | T-INV-BARCODE-01, T-INV-BARCODE-02, T-INV-BARCODE-03, T-INV-BARCODE-04, T-INV-BARCODE-05, T-INV-BARCODE-06 | G4 |
| I4.1 | Almacenar y escanear | FULL | FULL (mandatory spec) | T-INV-BARCODE-03, T-INV-BARCODE-05 | G2 |
| I4.2 | Generación | MISSING | FULL (mandatory spec) | T-INV-BARCODE-01, T-INV-BARCODE-02 | G2 |
| I4.3 | Impresión de etiquetas | MISSING | FULL (mandatory spec) | T-INV-BARCODE-04, T-INV-BARCODE-06 | G4 |
| I5 | Recetas y/o lista de materiales | PARTIAL | FULL (mandatory spec) | T-INV-BOM-01, T-INV-BOM-02, T-INV-BOM-03, T-INV-BOM-04 | G3 |
| I5.1 | Kits | FULL | FULL (mandatory spec) | T-INV-BOM-03 | G2 |
| I5.2 | Receta / BoM de producción | ADVANCED-ONLY | FULL (mandatory spec) | T-INV-BOM-01, T-INV-BOM-02, T-INV-BOM-04, T-INV-XFORM-05 | G3 |
| I6 | Transformaciones por presentación | MISSING | FULL (mandatory spec) | T-INV-XFORM-01, T-INV-PRES-02 | G2 |
| I7 | Transformación por porcionado, directa y con plantillas | MISSING | FULL (mandatory spec) | T-INV-XFORM-02, T-INV-XFORM-03 | G2 |
| I8 | Transformaciones manuales | MISSING | FULL (mandatory spec) | T-INV-XFORM-04, T-INV-XFORM-06, T-INV-XFORM-07, T-INV-XFORM-08, T-INV-XFORM-09 | G3 |
| I9 | Solicitud interna, consolidación, traslado automático y/o orden de compra | PARTIAL | FULL (mandatory spec) | T-INV-REQ-01, T-INV-REQ-02, T-INV-REQ-03, T-INV-REQ-04, T-INV-REQ-05, T-INV-REQ-06, T-INV-REQ-07 | G3 |
| I9.1 | Solicitud interna | PARTIAL | FULL (mandatory spec) | T-INV-REQ-01 | G2 |
| I9.2 | Consolidación | MISSING | FULL (mandatory spec) | T-INV-REQ-02 | G2 |
| I9.3 | Traslado automático | PARTIAL | FULL (mandatory spec) | T-INV-REQ-01, T-INV-REQ-04 | G2 |
| I9.4 | Orden de compra para cumplir | PARTIAL | FULL (mandatory spec) | T-INV-REQ-03, T-INV-REQ-06 | G2 |
| I10 | Ciclo completo de compra | PARTIAL | FULL (mandatory spec) | T-PUR-CYCLE-01, T-PUR-CYCLE-02, T-PUR-CYCLE-03, T-PUR-CYCLE-04, T-PUR-CYCLE-05, T-PUR-CYCLE-06, T-PUR-CYCLE-07, T-PUR-CYCLE-08 | G3 |
| I10.1 | Solicitud interna (opcional) | FULL | FULL (mandatory spec) | T-INV-REQ-01, T-PUR-CYCLE-07 | G2 |
| I10.2 | Solicitud de cotización | FULL | FULL (mandatory spec) | T-PUR-CYCLE-01 | G2 |
| I10.3 | Recepción de cotizaciones | PARTIAL | FULL (mandatory spec) | T-PUR-CYCLE-01, T-PUR-CYCLE-06 | G2 |
| I10.4 | Comparación de cotizaciones | FULL | FULL (mandatory spec) | T-PUR-CYCLE-02, T-PUR-CYCLE-08 | G3 |
| I10.5 | Recomendación de selección | MISSING | FULL (mandatory spec) | T-PUR-CYCLE-03, T-PUR-CYCLE-04 | G2 |
| I10.6 | Orden de compra | FULL | FULL (mandatory spec) | T-PUR-CYCLE-05 | G2 |
| I10.7 | Recepción parcial o total | FULL | FULL (mandatory spec) | T-PUR-CYCLE-05, T-PUR-CYCLE-07 | G2 |
| V1 | Precios formulados de servicios/productos | MISSING | FULL (mandatory spec) | T-SAL-PRICING-01, T-SAL-PRICING-02, T-SAL-PRICING-03, T-SAL-PRICING-04, T-SAL-PRICING-05, T-FORMULA-ENGINE-01, T-FORMULA-ENGINE-02 | G2 |
| V2 | Arquitectura abierta para otros Proveedores Tecnológicos | FULL | FULL (mandatory spec) | T-PT-OPEN-01, T-PT-OPEN-02 | G2 |
| N1 | Conceptos formulados (devengos o descuentos) | PARTIAL | FULL (mandatory spec) | T-PAY-FORMULA-01, T-PAY-FORMULA-02, T-PAY-FORMULA-03, T-PAY-FORMULA-04, T-PAY-FORMULA-05 | G3 |
| N1.1 | Maestro de conceptos y motor | FULL | FULL (mandatory spec) | T-PAY-FORMULA-01, T-PAY-FORMULA-02 | G2 |
| N1.2 | Fórmulas definidas por el usuario | MISSING | FULL (mandatory spec) | T-PAY-FORMULA-01, T-PAY-FORMULA-04, T-FORMULA-ENGINE-02 | G2 |
| N1.3 | Parámetros legales | EXTERNAL-PARAMETER-BLOCKED | FULL (mandatory spec; engine + synthetic fixture; official legal data EXTERNAL, does not block DEV) | T-PAY-FORMULA-03 | G2 |
| P1 | Portal de autoservicio de proveedores | ADVANCED-ONLY | FULL (mandatory spec) | T-PORTAL-SUP-01, T-PORTAL-SUP-02, T-PORTAL-SUP-03, T-PORTAL-SUP-04, T-PORTAL-SUP-05, T-PORTAL-SUP-06, T-PORTAL-CORE-01, T-PORTAL-CORE-02, T-PORTAL-CORE-03, T-PORTAL-CORE-04, T-PORTAL-CORE-05 | G4 |
| P2 | Portal de autoservicio de clientes | MISSING | FULL (mandatory spec) | T-PORTAL-CUS-01, T-PORTAL-CUS-02, T-PORTAL-CUS-03, T-PORTAL-CUS-04, T-PORTAL-CUS-05, T-PORTAL-CUS-06, T-PORTAL-CORE-01, T-PORTAL-CORE-02, T-PORTAL-CORE-03, T-PORTAL-CORE-04, T-PORTAL-CORE-05 | G4 |
| P3 | Portal de autoservicio de empleados | FULL | FULL (mandatory spec) | T-PORTAL-EMP-01, T-PORTAL-EMP-02, T-PORTAL-EMP-03, T-PORTAL-EMP-04, T-PORTAL-CORE-01, T-PORTAL-CORE-02, T-PORTAL-CORE-03, T-PORTAL-CORE-04, T-PORTAL-CORE-05 | G4 |

## Completion rule
A gate is accepted only when every check whose gate is ≤ that gate is PASSED in the gate's feature results (`erpctl` enforces it for 2.2.0-dev runs).
DEV_RUNNING requires every check (including DEV runtime checks) PASSED on the RC commit (D-034). A document claiming completion without test results is not evidence.
