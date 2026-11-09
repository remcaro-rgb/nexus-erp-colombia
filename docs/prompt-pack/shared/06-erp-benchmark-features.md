# Leading-ERP Features and Practices to Adopt

`[P]` = must-have for the release candidate. `[A]` = advanced: implemented behind a feature flag (default off), or delivered as a documented extension point with tests.
Benchmarks: SAP S/4HANA, Oracle NetSuite, Microsoft Dynamics 365 Business Central, Odoo 18, ERPNext (sources BM-01..BM-06b in shared/05; SAP Business One was not re-opened and is not relied upon).

## Cross-cutting
| Practice | Seen in | Adopt as | Tier |
|---|---|---|---|
| Single universal journal with all dimensions on each line (company, branch, cost center, project, party, item, analytic tags) | SAP ACDOCA, BC dimensions | `ledger_journal_line` carries dimension columns + JSONB extra dims | [P] |
| Perpetual inventory, with each stock move generating valuation and a GL entry in-line | ERPNext, Odoo automated valuation | Constitution C1-2; shared/07 | [P] |
| GRNI / stock received not billed and interim accounts | ERPNext (SRBNB), Odoo (stock interim) | Account `GRNI` in posting matrix | [P] |
| Document flow and drill-down: every document links to its source, its target, and its journal entries | SAP document flow, NetSuite related records | `DocumentRef` graph + "Related" side panel | [P] |
| Configurable approvals with amount thresholds and SoD | NetSuite SuiteFlow, BC workflows | P07 workflow engine | [P] |
| Saved views, user-defined filters, column chooser, inline grid editing, keyboard-first | NetSuite saved searches, Odoo list views | P08 data grid | [P] |
| Custom fields per entity with permissions | NetSuite custom fields, Odoo Studio | `custom_fields` schema with field policy integration | [P] |
| Multi-company in one tenant, with company switching and data scoped by company | All | ADR-003 | [P] |
| Intercompany with automatic mirror documents and elimination | NetSuite AIM, BC | P16 | [P] auto-mirror, [A] auto-elimination with partial ownership |
| Consolidation with currency translation methods (closing, average, historical), minority interest | BC (business units), NetSuite | P16 | [A] |
| Multi-book (NIIF vs fiscal) and tax-difference register | SAP parallel ledgers | P05 ledger `book` dimension | [P] fiscal + NIIF books; [A] more books |
| Period close checklist and close cockpit | NetSuite period close, SAP Financial Closing cockpit | P05/P07 | [P] |
| Audit trail at field level with who, when, old→new, and channel (UI/API/AI/MCP/import) | All | P07 audit log with hash chain | [P] |
| Sandbox / company copy for testing | NetSuite sandbox, Odoo duplicate | Tenant clone into a sandbox environment | [A] |
| Open API, webhooks, and event subscriptions | NetSuite REST/SuiteTalk, BC APIs | P02 OpenAPI + webhooks (P07) | [P] |
| Report builder plus pivot plus scheduled delivery | NetSuite, Odoo spreadsheets | P17 | [P] |
| Embedded analytics KPIs per role (home dashboards) | BC role centers | P08/P17 role homes | [P] |

## Finance
Bank reconciliation with matching rules and an AI suggestion `[P]`; payment runs with approval `[P]`; aged AR/AP `[P]`; FX revaluation of open items `[P]`;
budgets vs actuals by dimension `[P]`; recurring journal templates `[P]`; accruals with auto-reversal `[P]`; cash-flow forecast `[A]`; deferred revenue
and expense schedules `[A]`; dunning `[A]`; IFRS 16 leases `[A]`.

## Inventory and costing
Multi-warehouse and bins `[P]`; lots/serials with expiry (FEFO) `[P]`; UoM conversions `[P]`; reorder points / min-max `[P]`; landed cost allocation (by value, qty,
weight, volume) `[P]`; cycle counts `[P]`; transfers with in-transit `[P]`; kits/bundles `[P]`; standard cost `[A]`; light manufacturing BoM/assembly `[A]`;
demand forecasting `[A]`.

## Sales/CRM
Leads → opportunities → quotes → orders → deliveries → invoices pipeline `[P]`; price lists, discounts, and promotions `[P]`; credit limits with blocking `[P]`; activity timeline, email
logging, and tasks `[P]`; customer 360 `[P]`; returns (RMA) with credit notes `[P]`; commissions `[A]`; marketing campaigns `[A]`; quote e-signature `[A]`.

## Purchasing
Requisitions → RFQ → PO → receipt → vendor invoice three-way match with tolerances `[P]`; vendor portal `[A]`; blanket orders `[A]`.

## POS
Touch UI, barcode scanning, **online finalization with in-line GL/stock** (ADR-015; a lost connection only allows browsing and unposted draft carts), true offline fiscal selling `[A]` behind EG-13 (common in competitors, but it conflicts with the in-line requirement), cash sessions with open/close and blind counts, multiple tenders, returns with original ticket, DEE POS electronic,
conversion to FE on request, customer lookup `[P]`; loyalty `[A]`; restaurant mode `[A]`.

## HR/Payroll/Time
Employee master with contracts and history, periods (monthly/biweekly), payroll concepts engine, novelties (novedades), provisions, social security PILA export, nómina
electrónica, liquidation on termination, payslips, employee self-service `[P]`; time clock (web, kiosk, mobile, with geofence option; biometric only with explicit consent),
shift planning, overtime approval `[P]`; recruiting and performance `[A]`.

## Fixed assets
Asset register, acquisition from AP or capitalization from inventory/projects, depreciation methods (straight-line, declining balance, units of production) per book, componentization,
revaluation and impairment (NIIF), disposals with gain/loss, transfers between cost centers, physical-count tags and QR `[P]`; leases `[A]`.

## UX principles (applied in P08)
1. Speed: p95 page interactive < 2.5 s on mid-range laptops; grid of 10k rows virtualized; optimistic UI only for non-financial actions.
2. Keyboard-first data entry, a command palette (Ctrl/Cmd+K) with AI, and consistent shortcuts.
3. Progressive disclosure: simple defaults with an "Advanced" section; never more than one primary action per view.
4. Inline validation with server-side truth; problem+json errors mapped to fields.
5. Clear posting state badges (Draft, Pending approval, Posted, Reversed, Fiscal: pending/accepted/rejected/contingency).
6. Accessibility WCAG 2.2 AA, including focus visibility, target size, and no drag-only interactions.
7. es-CO formats: `$ 1.234.567,89` COP display, dates `dd/mm/aaaa`, NIT with DV validation.
8. Empty states with a guided action and AI onboarding entry.
