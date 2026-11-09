# Deterministic Seed Scenarios (cross-module golden tests)

Location: `tests/cross_module/scenarios/S01_*.py` … (owned by P22; module owners contribute by CCR). Data fixtures: `backend/kernel/fixtures/demo_tenant.yaml` (P21).
All amounts are in COP unless stated. **Rates marked (FIX) are FIXTURE values for engine mechanics, NOT legal values.** Legal golden cases (`tests/regulatory/`)
are added by P06/P13 only after the parameter has been re-verified from an official source (shared/04). Each scenario asserts: document states, stock quantities,
valuation, exact journal lines (role, Dr, Cr, dimensions), sub-ledger = GL reconciliation, fiscal document status in sandbox, and the audit log entries.

## Demo tenant
* Tenant `demo-andina`. Companies: **A "Comercial Andina S.A.S."** (NIT 900.000.001-?, DV computed by algorithm; NIIF Group 2; income-tax declarant; VAT responsible; Bogotá ICA);
  **B "Distribuciones del Valle S.A.S."** (NIIF Group 2; Cali). Both in COP, timezone America/Bogota. Fiscal mode: sandbox.
* Parameters (2026 fixture set mirrors shared/04 values): UVT 52,374; SMMLV 1,750,905 (DISPUTED flag on); transport allowance 249,095.
* Users: `admin@A`, `contador@A` (accounting, approve), `vendedor1@A` (sales, own customers only, sees no cost fields), `bodega@A` (warehouse BOG-01 only),
  `nomina@A` (payroll; salary fields visible), `gerente@B`, `auditor@tenant` (read-only all companies, PII masked).

## S1 – Sale with VAT and customer withholdings (WAC item) [P10, P09, P05, P06, P15]
1. Item `CAF-500` (coffee 500 g), WAC, opening 0. Company A, warehouse BOG-01.
2. S2 receipts run first (see S2), so WAC = 11,000 when the sale happens.
3. Sales invoice to customer `Hoteles Sabana S.A.S.` (withholding agent): 120 units × 20,000 = **2,400,000** net; VAT 19% = **456,000**; total **2,856,000**.
   Delivered on invoice.
4. Expected journal (A): Dr AR 2,856,000 / Cr REVENUE 2,400,000 / Cr VAT_PAYABLE 456,000 ; Dr COGS 1,320,000 / Cr INVENTORY 1,320,000.
5. The customer pays withholding (FIX rates): renta 2.5% → 60,000; ReteIVA 15% of VAT → 68,400; ReteICA 9.66‰ → 23,184. Receipt **2,704,416**.
   Journal: Dr BANK 2,704,416 / Dr WHT_RECEIVABLE_RENTA 60,000 / Dr WHT_RECEIVABLE_IVA 68,400 / Dr WHT_RECEIVABLE_ICA 23,184 / Cr AR 2,856,000.
6. FE generated (UBL), CUFE computed, sandbox accepted, AttachedDocument to customer, fiscal status `accepted`.
7. Leak checks: `vendedor1@A` sees the invoice but not COGS or margin fields (API, export, report, AI, MCP).

## S2 – Purchases, WAC vs FIFO, GRNI, price difference [P11, P09, P05]
1. Receipt R1: 100 × 10,000 → Dr INVENTORY 1,000,000 / Cr GRNI 1,000,000.
2. Receipt R2: 50 × 13,000 → Dr INVENTORY 650,000 / Cr GRNI 650,000. WAC = (1,000,000 + 650,000)/150 = **11,000.00000000**.
3. Sale of 120 (S1). COGS WAC = **1,320,000**; FIFO variant (run the same scenario on item `CAF-500F` with FIFO) = 100×10,000 + 20×13,000 = **1,260,000**.
   Remaining: WAC 30 × 11,000 = 330,000 ; FIFO 30 × 13,000 = 390,000.
4. Vendor invoice for R1 at the receipt price: Dr GRNI 1,000,000 / Dr VAT_RECOVERABLE 190,000 / Cr AP 1,190,000 − withholding (FIX: renta compras 2.5% = 25,000)
   → Cr AP 1,165,000 / Cr WHT_PAYABLE 25,000.
5. Vendor invoice for R2 at **13,500** (Δ = 500/unit, 25,000 total) after the sale: on hand of the item = 30 ≤ 50 received →
   Dr GRNI 650,000 / Dr INVENTORY 15,000 / Dr COGS 10,000 / Dr VAT_RECOVERABLE 128,250 / Cr AP 786,375 / Cr WHT_PAYABLE 16,875 (2.5% of 675,000).
   New WAC = (330,000 + 15,000)/30 = **11,500**. FIFO: the remaining layer becomes 30 × 13,500 = 405,000.
6. Assert GRNI balance = 0 and inventory GL = valuation layers.

## S3 – Landed cost allocation [P11, P09]
Receipt of item X 200 × 5,000 (1,000,000) and item Y 100 × 20,000 (2,000,000). Freight invoice 300,000 + VAT 57,000.
* By value: X 100,000 (unit +500), Y 200,000 (unit +2,000). By quantity: X 200,000, Y 100,000.
* Variant: 50 units of Y sold before the freight posts (value basis): Y share 200,000 → 100,000 to INVENTORY, 100,000 to COGS.
* Rounding: allocation of 100,000 over 3 lines of 1/3 each → 33,333.3333 / 33,333.3333 / 33,333.3334 (last line takes the residual).

## S4 – POS session: online checkout, disconnected draft blocked, reconnect atomic commit, return [P12, P15, P09, P05]
Opening float 200,000. Ticket T1 cash 119,000 (net 100,000 + VAT 19,000) and T2 card 59,500 check out online: each is one transaction (number, stock, GL, AR/cash, outbox).
**Disconnect:** the terminal loses its connection to the server. The cashier builds T3 (cash 23,800). Assert: the checkout and tender buttons are disabled with the message
"Sin conexión"; T3 is saved locally as an unposted draft watermarked "BORRADOR – sin valor fiscal"; server counts unchanged: **0** new journal entries, **0** stock moves,
**0** numbers consumed, **0** fiscal documents, and no receipt print is possible.
**Reconnect:** T3 is resubmitted → the server recomputes (same price → no diff) and commits atomically (number, stock, GL, outbox). Failure-injection variant: fail at the stock step →
nothing persisted, the draft is kept, and a retry with the same Idempotency-Key → exactly one ticket. Price-change variant: the server price differs → the difference is shown and the cashier must confirm before commit.
Return of T3 (cash refund 23,800, online). Expected cash 319,000; counted 318,500 → Dr CASH_DIFFERENCE 500 / Cr CASH_POS 500.
**DIAN-down variant (server online):** checkout still commits in-line; the DEE POS goes to P15's contingency queue and the 48 h timer is asserted.
T2 is converted to an FE on buyer request (buyer data per R-FE-03). The flag `pos_offline_fiscal_selling` stays **off** throughout.

## S5 – Concurrency and failure injection [P09, P10, P22]
Item with 10 on hand, negative stock disallowed. Two parallel invoices of 8 units each (barrier-synchronized threads) → exactly one is posted, the other fails with
`409 insufficient_stock`, and **zero** GL rows, stock moves, or outbox events exist for the failed one. Failure injection: raise after the stock move insert and before journal
insert → full rollback. Duplicate POST with the same Idempotency-Key → same response, one document.

## S6 – Monthly payroll, one employee (FIX rates; legal golden pending EG-7) [P13, P05, P15]
Employee: salary 3,000,000 (ordinary), ARL class I, month June 2026, 30 days, no novelties. Company A is exonerated (ET 114-1) because the employee earns < 10 SMMLV.
* Transport allowance: 3,000,000 ≤ 2 × 1,750,905 = 3,501,810 → **249,095**.
* Employee deductions: health 4% = 120,000; pension 4% = 120,000; FSP 0 (< 4 SMMLV). Withholding: (3,000,000 − 240,000) × 75% = 2,070,000 = 39.52 UVT → **0**.
* Net pay = 3,000,000 + 249,095 − 240,000 = **3,009,095**.
* Employer: pension 12% = 360,000; health 0 (exonerated); SENA 0; ICBF 0; CCF 4% = 120,000; ARL 0.522% = 15,660.
* Provisions: cesantías (3,249,095/12) = 270,757.92; interest on cesantías 12%/12 of the accrual = 2,707.58; prima 270,757.92; vacations 3,000,000 × 15/360 = 125,000.
* Journal lines per shared/07 §4; nómina electrónica XML (NominaIndividual) generated, CUNE computed, transmission scheduled before the 10th of July.

## S7 – Time-keeping boundary tests (parameters per R-LB-04/05; values to confirm by legal review) [P13]
| Case | Work | Expected rule applied |
|---|---|---|
| T7-1 | Wed 2025-12-24 19:00–22:00 | night window starts 21:00 (pre-reform) → 1 night hour |
| T7-2 | Thu 2025-12-25 (holiday) 19:00–22:00 | night window 19:00 → 3 night hours + holiday surcharge 80% |
| T7-3 | Sun 2026-06-28 8 h (14:00–22:00) | Sunday surcharge 80%; weekly max 44 h; divisor parameter for 44 h |
| T7-4 | Sun 2026-07-05 same shift | Sunday surcharge **90%**; weekly max still 44 h (42 h starts 2026-07-15) |
| T7-5 | Sun 2026-07-19 same shift, salary 3,000,000, divisor 210 (B-12) | hourly 14,285.71; Sunday 8 h × 90% = 102,857.14; night 3 h × 35% = 15,000.00 |
| T7-6 | Sun 2027-07-04 | Sunday surcharge **100%**; blocked if 2027 SMMLV is UNKNOWN (fail closed) |
| T7-7 | Third Sunday worked in a month | habitual flag → compensatory rest rules |

## S8 – Fixed asset life cycle (two books) [P14, P11, P05]
Laptop bought 2026-03-10 via vendor invoice: cost 4,800,000 + VAT 912,000 (policy: VAT deducted as recoverable; parameter). NIIF book: straight line, 36 months, salvage 0,
start the following month → 133,333.33 per month (the last month takes the residual so total = 4,800,000). Fiscal book: 5 years (FIX rate 20%/year) → 80,000 per month.
Depreciation April–November 2026 (8 months): NIIF accumulated 1,066,666.64; fiscal 640,000; the tax-difference register shows a temporary difference of 426,666.64.
Sale on 2026-12-31 for 4,000,000 + VAT 760,000, after December depreciation (9 months NIIF = 1,199,999.97, NBV 3,600,000.03) → NIIF gain 399,999.97.

## S9 – Documento soporte with weekly accumulation [P11, P15, P06]
Services from a non-obligated individual: Mon 400,000; Wed 350,000; Fri 250,000 (weekly accumulation chosen). Withholding services (FIX 4%; base 2 UVT = 104,748 under
Decreto 572 parameters, DISPUTED) applies to each payment ≥ base. One DS transmitted on the last business day of the week for 1,000,000. If that Friday is a holiday, the
job uses the previous business day (calendar).

## S10 – Intercompany sale A → B [P16, P10, P11, P09]
A sells 10 × 15,000 to B (cost 11,500). A: Dr IC_RECEIVABLE 178,500 / Cr REVENUE_IC 150,000 / Cr VAT_PAYABLE 28,500 ; Dr COGS 115,000 / Cr INVENTORY 115,000.
B (auto-mirrored, same transaction): goods receipt and vendor invoice Dr INVENTORY 150,000 / Dr VAT_RECOVERABLE 28,500 / Cr IC_PAYABLE 178,500.
Consolidation [A]: eliminate IC revenue/COGS and unrealized profit in B's ending inventory (35,000 if unsold).

## S11 – Onboarding from spreadsheets [P21, P18, P05]
Input pack `tests/fixtures/onboarding/andina_xlsx/`: CoA (PUC template + 40 custom accounts), 50 customers (5 with invalid NIT DV), 30 suppliers, 200 items
(opening qty/cost), 12 open AR invoices (45,000,000), 8 open AP invoices (22,000,000), bank 30,000,000, 5 fixed assets with accumulated depreciation, opening date 2026-10-31.
Expected: the agent detects the 5 invalid DVs and proposes fixes; the dry-run report; opening entry balanced against an `OPENING_BALANCE_EQUITY` role; AR/AP/inventory
sub-ledgers reconcile; the user confirms each batch; the first invoice is issued in sandbox.

## S12 – Foreign currency payable [P11, P16, P05]
Vendor invoice USD 1,000 at TRM 4,000 → AP 4,000,000. Month-end TRM 4,100 → Dr UNREALIZED_FX_LOSS 100,000 / Cr AP 100,000 (auto-reversed on day 1).
Payment at TRM 4,050 → Dr AP 4,000,000 / Dr FX_REALIZED_LOSS 50,000 / Cr BANK 4,050,000. TRM source: Banco de la República/Superfinanciera daily rate (adapter; fixture in tests).

## §AI – Agent evaluation suite (40 tasks, P19)
Categories (≥5 each): data Q&A ("sales by city last quarter vs previous"), drill-down explanation ("why did margin drop in September?"), process execution with confirmation
("create a PO for the items below reorder point at preferred vendors"), scheduling ("every Monday 7am email me AR aging over 60 days"), report building (save as a report),
document generation (a collection letter as PDF from a template), simulation/what-if ("if the minimum wage rose 9% in 2027, payroll cost?" → runs on a sandbox
copy of parameters, never posts), and refusals/permission (a user without payroll access asks for salaries → refusal without leaking aggregates below the k-anonymity threshold of 5).
Graded by deterministic checks on tool calls and results plus a rubric; run with ≥ 2 providers.

## §Payroll legal golden cases (added after EG-7)
P13 must add ≥ 25 cases signed off by the reviewing professional (integral salary, part-time, incapacity days 1–2/3+, maternity leave, vacations taken, termination with and
without just cause, salary ≥ 4 SMMLV with FSP, ≥ 10 SMMLV non-exonerated, apprentices).
