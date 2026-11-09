# Posting Matrix, Valuation Rules and Invariants

Accounts are **logical roles** resolved by account determination (ADR-005). They are not hard-coded account numbers. The PUC template (P21 seed) maps roles to defaults,
for example `AR`→130505, `INVENTORY`→1435, `GRNI`→2335xx (configurable), `VAT_PAYABLE`→2408, `VAT_RECOVERABLE`→240802 (or 2408 debit), `WHT_PAYABLE`→2365,
`WHT_RECEIVABLE`→135515, `ICA_WHT_PAYABLE`→2368, `REVENUE`→4135, `COGS`→6135, `PAYROLL_PAYABLE`→2505, `SS_PAYABLE`→2370, `PROVISIONS`→2610/2510…,
`ACCUM_DEPRECIATION`→1592, `DEPRECIATION_EXPENSE`→5160. **The PUC mapping is a template that the contador must review (EG-8).**

Notation: Dr = debit, Cr = credit. "in-line" = same DB transaction as the business document (C1-1/C1-2).

## 1. Sales and AR (P10, P12)
| Trigger (state transition) | Stock effect (in-line) | GL entry (in-line) |
|---|---|---|
| Quote/Sales order created/confirmed | reservation only (no valuation) | none |
| Delivery (picking validated) | Issue qty; consume valuation layers at cost C | Dr COGS C / Cr INVENTORY C |
| Customer invoice posted (goods already delivered) | none | Dr AR (gross − WHT suffered if applied on invoice) ; Dr WHT_RECEIVABLE (if the customer is a withholding agent and the company opts to pre-register) ; Cr REVENUE net ; Cr VAT_PAYABLE |
| Invoice posted with "deliver on invoice" (direct sale, POS) | Issue | Both entries above in one transaction |
| Customer invoice before delivery (anticipated billing) | none | Dr AR / Cr REVENUE_DEFERRED or UNEARNED (policy) / Cr VAT_PAYABLE; on delivery Dr COGS / Cr INVENTORY and, if policy, Dr REVENUE_DEFERRED / Cr REVENUE |
| Credit note (return with goods) | Receipt back at **original issue cost** (from the linked delivery layers) | Dr REVENUE / Dr VAT_PAYABLE / Cr AR ; Dr INVENTORY / Cr COGS |
| Credit note (price discount, no goods) | none | Dr SALES_DISCOUNTS (or REVENUE) / Dr VAT_PAYABLE / Cr AR |
| Debit note | none | Dr AR / Cr REVENUE / Cr VAT_PAYABLE |
| Customer receipt | none | Dr BANK / Dr WHT_RECEIVABLE (withholdings certified by customer: renta, IVA, ICA) / Cr AR ; bank fees and GMF as separate lines |
| Receipt in foreign currency | none | as above at the receipt-date rate; realized FX diff: Dr/Cr FX_REALIZED_LOSS/GAIN against AR |
| Write-off | none | Dr BAD_DEBT_EXPENSE / Cr AR (requires approval) |
| Customer advance (anticipo) | none | Dr BANK / Cr CUSTOMER_ADVANCES ; applied on invoice: Dr CUSTOMER_ADVANCES / Cr AR |
| POS cash session close | none | Cash over/short: Dr/Cr CASH_DIFFERENCE vs CASH_POS ; transfer to safe/bank: Dr BANK_TRANSIT / Cr CASH_POS |
| E-invoice rejected by DIAN | none | **No automatic GL reversal.** The document goes to `fiscal_status=rejected`; the user corrects it and re-issues (same number when the annex allows, otherwise a new doc + credit note). Accounting stays as posted until a corrective document posts |

## 2. Purchasing and AP (P11)
| Trigger | Stock | GL |
|---|---|---|
| Requisition / RFQ / PO | none (PO adds expected qty) | none |
| Goods receipt (stockable) | Receipt; create valuation layer at PO price P (in company currency at receipt date rate) | Dr INVENTORY P / Cr GRNI P |
| Vendor invoice matched to receipt, same price | none | Dr GRNI P / Dr VAT_RECOVERABLE (if deductible; otherwise capitalized into cost) / Cr AP / Cr WHT_PAYABLE (renta, ReteIVA, ReteICA per tax matrix) |
| Vendor invoice price ≠ receipt price (Δ) | if the receipted qty is still on hand: revalue remaining layers by Δ×qty_on_hand; the remainder goes to COGS (Odoo/ERPNext practice) | Dr GRNI P ; Dr/Cr INVENTORY Δ_onhand ; Dr/Cr COGS (or PPV) Δ_consumed ; ... |
| Vendor invoice for services/expenses | none | Dr EXPENSE (by dimension) / Dr VAT_RECOVERABLE / Cr AP / Cr WHT_PAYABLE |
| Documento soporte (non-obligated supplier) | per item type | same as vendor invoice + ReteIVA rules for régimen simple/no responsable per tax matrix; fiscal doc generated (R-FE-08) |
| Landed cost (freight, duties, insurance) posted and allocated | revalue receipt layers proportionally (allocation basis); the portion for consumed qty goes to COGS | Dr INVENTORY (on-hand portion) / Dr COGS (consumed portion) / Cr AP or LANDED_COST_CLEARING |
| Vendor return | Issue at **original receipt layer cost** | Dr GRNI or AP / Cr INVENTORY ; plus the vendor credit note: Dr AP / Cr GRNI / Cr VAT_RECOVERABLE |
| Payment to vendor | none | Dr AP / Cr BANK ; FX realized diff; GMF expense line |
| Vendor advance | none | Dr VENDOR_ADVANCES / Cr BANK |
| GRNI aging | none | report only; period-end accrual is not needed because GRNI is already recognized |

## 3. Inventory internal (P09)
| Trigger | Stock | GL |
|---|---|---|
| Transfer between warehouses (same company, company-wide valuation) | Issue + receipt (or in-transit) | none (if valuation is per warehouse: Dr INVENTORY_wh2 / Cr INVENTORY_wh1 at cost) |
| Transfer between companies | Treated as an intercompany sale/purchase (P16) | intercompany documents |
| Inventory adjustment (count) | +/− qty | gain: Dr INVENTORY / Cr INVENTORY_ADJ_GAIN ; loss: Dr INVENTORY_ADJ_LOSS / Cr INVENTORY |
| Scrap | issue | Dr SCRAP_EXPENSE / Cr INVENTORY |
| Consumption to cost center / project | issue | Dr EXPENSE (dimension) / Cr INVENTORY |
| Manual revaluation (allowed only with approval, NIIF NRV impairment) | value only | Dr INVENTORY_IMPAIRMENT_EXPENSE / Cr INVENTORY_ALLOWANCE (contra) |
| Kit sale | issue components | COGS = sum of component costs |
| Negative stock correction (ADR-008) | value only on receipt | Dr/Cr COGS vs INVENTORY for the difference between provisional cost and actual |

## 4. Payroll (P13)
| Trigger | GL |
|---|---|
| Payroll run approved/posted (per period, per employee, summarized per dimension) | Dr SALARY_EXPENSE (by concept: basic, overtime, surcharges, commissions, transport allowance) / Cr PAYROLL_PAYABLE (net) / Cr SS_PAYABLE_EMPLOYEE (health 4%, pension 4%, FSP) / Cr WHT_PAYABLE (labor withholding) / Cr OTHER_DEDUCTIONS_PAYABLE (loans, embargos, union) |
| Employer contributions (same transaction) | Dr SS_EXPENSE (health employer if not exonerated, pension 12%, ARL, CCF 4%, SENA/ICBF if not exonerated) / Cr SS_PAYABLE (by entity: EPS, AFP, ARL, CCF) |
| Provisions (monthly) | Dr BENEFITS_EXPENSE / Cr PROVISION_CESANTIAS, PROVISION_INT_CESANTIAS, PROVISION_PRIMA, PROVISION_VACACIONES |
| Payment of net pay | Dr PAYROLL_PAYABLE / Cr BANK |
| PILA payment | Dr SS_PAYABLE / Cr BANK |
| Cesantías deposit to fund / payment of prima, interest, vacations | Dr PROVISION_* (and Dr BENEFITS_EXPENSE for the shortfall/excess adjustment) / Cr BANK or PAYROLL_PAYABLE |
| Termination liquidation | Dr PROVISION_* / Dr INDEMNITY_EXPENSE / Cr PAYROLL_PAYABLE |
| Nómina electrónica ajuste (replace/delete) | If the amounts change: a reversing + new payroll entry for the delta; the fiscal note goes via P15 |

## 5. Fixed assets (P14)
| Trigger | GL |
|---|---|
| Acquisition via vendor invoice line with asset category | Dr FIXED_ASSET_COST / Dr VAT (capitalize or deduct per ET rules; parameter) / Cr AP |
| Capitalization from inventory / construction in progress | Dr FIXED_ASSET_COST / Cr INVENTORY or CIP |
| Depreciation run (monthly, per book) | Dr DEPRECIATION_EXPENSE (dimension) / Cr ACCUM_DEPRECIATION; **separate entries per book** (NIIF vs fiscal) in the respective book ledger |
| Revaluation (NIIF, Group 1/2 policy) | Dr FIXED_ASSET_COST or Cr ACCUM_DEP / Cr REVALUATION_SURPLUS (OCI) |
| Impairment | Dr IMPAIRMENT_EXPENSE / Cr ACCUM_IMPAIRMENT |
| Disposal / sale | Dr ACCUM_DEPRECIATION / Dr ACCUM_IMPAIRMENT / Dr AR or BANK (proceeds) / Cr FIXED_ASSET_COST / Cr VAT_PAYABLE (if a sale) / Dr LOSS or Cr GAIN_ON_DISPOSAL |
| Transfer between cost centers | Reclass by dimension (Dr/Cr same accounts, different dimension) |

## 6. Treasury, FX, intercompany, close (P16, P05)
| Trigger | GL |
|---|---|
| Bank transfer between own accounts | Dr BANK_B / Cr BANK_A (or via BANK_TRANSIT if the dates differ) |
| Bank statement line matched to an open item | no new entry (reconciliation flag) ; unmatched fees: Dr BANK_FEES / Cr BANK |
| Month-end FX revaluation of open foreign-currency items and bank accounts | Dr/Cr UNREALIZED_FX vs AR/AP/BANK ; auto-reversal on the first day of the next period (policy) or delta method |
| Intercompany sale A→B | Company A: sales entries with party = IC partner and IC_RECEIVABLE ; Company B: purchase entries with IC_PAYABLE ; linked by `ic_link_id` ; **both commit in the same transaction** if both companies are in the same tenant |
| Year-end close | Dr REVENUE accounts / Cr INCOME_SUMMARY ; Dr INCOME_SUMMARY / Cr expense accounts ; net to RETAINED_EARNINGS (current year result) — generated by the close process; period locked |

## 7. Valuation algorithms
* **Weighted average (perpetual):** on receipt `new_avg = (qty_on_hand × avg + qty_in × unit_cost_in) / (qty_on_hand + qty_in)`, computed with NUMERIC(24,8) and rounded
  half-even to 8 decimals for the unit cost. Issue value = `round_half_even(qty × avg, 4)` in the amount precision; the residual-on-zero rule applies: when qty becomes 0,
  the remaining value is written off to COGS (rounding residual line) so value = 0 exactly.
* **FIFO:** layers `(receipt_ts, layer_id)` ordered; an issue consumes the oldest first; partial layer consumption keeps `remaining_qty` and `remaining_value`, where the
  last unit of a layer takes the residual value.
* **Back-dated transactions:** allowed only within the open period and only if no later issues exist for the item/company **or** the company enables "recost forward",
  which reprocesses later moves in a job and posts adjustment entries dated in the open period with links. Default: disallow back-dating before the last issue (safe).
* **Concurrency:** `SELECT ... FOR UPDATE` on `inventory_item_valuation(company_id, item_id)` rows (and on lot rows), always acquired in sorted `(item_id, warehouse_id, lot_id)` order
  across a document's lines to avoid deadlocks; retry on serialization or deadlock up to 3 times with jitter; idempotency key prevents double application.

## 8. Rounding policy
* Amounts are stored at 4 decimals internally; the **document totals presented and transmitted to DIAN follow the annex rules** (2 decimals for COP amounts in the
  representation; the annex defines the tolerance for line vs total rounding; P15 validates against the annex).
* Tax is computed per line, then summed (the default; configurable to per-document if the annex permits). Any difference between the sum of rounded lines and the document
  total goes to a `ROUNDING` account line, generated by the engine, never by the user. It is limited to a tolerance parameter (default COP 1.00 per document).
* FX: rates stored at 10 decimals; converted amounts rounded half-even to 4; the per-entry COP balance is enforced and a residual goes to the `FX_ROUNDING` line.

## 9. Invariants (property-based tests in P05, P09, P22)
1. ∀ journal entries: Σdebit = Σcredit in the transaction currency **and** in COP (company currency).
2. ∀ posted documents with a monetary effect: ∃ exactly one active journal entry (or an explicitly documented set) referencing it; reversal references the original.
3. GL balance(INVENTORY roles, company, date) = Σ valuation layers remaining value (company, date).
4. GL balance(AR) = Σ open AR items; GL(AP) = Σ open AP items; GL(GRNI) = Σ receipts not invoiced at receipt value ± price-difference policy.
5. GL(FIXED_ASSET_COST − ACCUM_DEP − ACCUM_IMP) per book = asset register NBV per book.
6. GL(PAYROLL/SS/PROVISION payables) = payroll sub-ledger liabilities.
7. Stock: qty_on_hand(item, wh, lot) = Σ moves; never < 0 unless the company allows negative stock; reserved ≤ on_hand + incoming (policy).
8. No posting into a closed or locked period; no UPDATE/DELETE on posted journal lines or stock moves (DB trigger + app check).
9. Document numbering: no gaps within DIAN-authorized ranges for fiscal documents (sequences allocated at post time under lock; voided docs keep their numbers).
10. Every fiscal document has exactly one current fiscal status and a complete event log.
11. Idempotency: replaying the same command with the same key returns the same result and produces no new rows.
12. Tenant isolation: for any principal P and any channel (API, export, report, AI, MCP, search, webhook, notification), the visible rows ⊆ policy(P).
