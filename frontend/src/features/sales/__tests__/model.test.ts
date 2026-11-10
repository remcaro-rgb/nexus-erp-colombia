import { describe, expect, it } from "vitest";
import type { PriceSnapshot, RepriceDiff } from "../api";
import { canReprice, explainRows, overdueShare, repriceSummary } from "../model";

const FORMULA: PriceSnapshot = {
  source: "FORMULA",
  resolved_source: "FORMULA",
  resolved_price: "30.11",
  unit_price: "30.11",
  currency: "USD",
  fx_rate: "4100.000000",
  rate_date: "2026-11-09",
  rounding: "ROUND_HALF_UP:2",
  detail: { formula_code: "precio_margen", formula_version: 1, inputs: { base_price: "100000" } },
};

describe("sales view model", () => {
  it("T-SAL-PRICING-05 explain shows the FX of the document date and the currency rounding", () => {
    const rows = explainRows(FORMULA);
    expect(rows.find((r) => r.label === "TRM")?.value).toBe("4100 (2026-11-09)");
    expect(rows.find((r) => r.label === "Redondeo")?.value).toBe("Mitad hacia arriba · decimales 2");
    expect(rows.find((r) => r.label === "Precio unitario")?.value).toBe("30.11 USD");
  });

  it("T-SAL-PRICING-04 explain renders only what the server sent (no cost input, never formula text)", () => {
    const rows = explainRows(FORMULA);
    expect(rows.some((r) => r.label.includes("cost"))).toBe(false);
    expect(JSON.stringify(rows)).not.toMatch(/expr|trace/);
    const withCost = explainRows({ ...FORMULA, detail: { ...FORMULA.detail, inputs: { cost: "600" } } });
    expect(withCost.find((r) => r.label === "Entrada · cost")?.value).toBe("600");
  });

  it("T-SAL-PRICING-02 reprice only on drafts; the diff summary is decimal-safe", () => {
    expect(canReprice({ document_status: "DRAFT" })).toBe(true);
    expect(canReprice({ document_status: "POSTED" })).toBe(false);
    const diff: RepriceDiff[] = [
      { line_no: 1, item_id: "a", old: { unit_price: "850", source: "LIST", amount: "10200" }, new: { unit_price: "820", source: "LIST", amount: "9840" }, delta_amount: "-360.0000", changed: true },
      { line_no: 2, item_id: "b", old: { unit_price: "0.1", source: "BASE", amount: "0.1" }, new: { unit_price: "0.1", source: "BASE", amount: "0.1" }, delta_amount: "0.0000", changed: false },
      { line_no: 3, item_id: "c", old: { unit_price: "0.1", source: "BASE", amount: "0.1" }, new: { unit_price: "0.3", source: "BASE", amount: "0.3" }, delta_amount: "0.2", changed: true },
    ];
    expect(repriceSummary(diff)).toEqual({ changed: 2, delta: "-359.8" });
  });

  it("overdue share has no base when the total is zero", () => {
    const totals = { current: "0", "1_30": "0", "31_60": "0", "61_90": "0", over_90: "0" };
    expect(overdueShare({ totals, total: "0" })).toBe("—");
    expect(overdueShare({ totals: { ...totals, current: "750", over_90: "250" }, total: "1000" })).toBe("25.0 %");
  });
});
