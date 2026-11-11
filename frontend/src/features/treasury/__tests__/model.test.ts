import { describe, expect, it } from "vitest";
import { allowedActions, fundTotal, lineKind } from "../model";

const cop = (amount: string) => ({ amount, currency: "COP" });

describe("treasury voucher model", () => {
  it("derives the line kind like the backend (open item, concept, tax code, advance)", () => {
    expect(lineKind({ line_no: 1, open_item_id: "x" })).toBe("APLICACION");
    expect(lineKind({ line_no: 2, concept_id: "c" })).toBe("CONCEPTO");
    expect(lineKind({ line_no: 3, tax_code: "RTE25" })).toBe("RETENCION");
    expect(lineKind({ line_no: 4, applied_amount: cop("1") })).toBe("ANTICIPO");
  });

  it("T-TRE-RC-01 fund total = applications + advance − withholding (decimal)", () => {
    const total = fundTotal([
      { line_no: 1, open_item_id: "a", applied_amount: cop("600000"), amount: cop("600000.0000") },
      { line_no: 2, open_item_id: "b", applied_amount: cop("1500000"), amount: cop("1500000.0000") },
      { line_no: 3, applied_amount: cop("400000"), amount: cop("400000.0000") },
      { line_no: 4, tax_code: "RTE25", unit_price: cop("2100000"), amount: cop("52500.0000") },
    ]);
    expect(total).toBe("2447500.0000");
  });

  it("offers actions by status and permission (the server still decides SoD)", () => {
    const all = () => true;
    expect(allowedActions({ status: "DRAFT" }, all, "treasury.receipt")).toEqual(["submit", "post"]);
    expect(allowedActions({ status: "SUBMITTED" }, all, "treasury.receipt")).toEqual(["approve", "reject"]);
    expect(allowedActions({ status: "POSTED" }, all, "treasury.receipt")).toEqual(["reverse"]);
    expect(allowedActions({ status: "POSTED" }, () => false, "treasury.receipt")).toEqual([]);
    expect(allowedActions({ status: "REVERSED" }, all, "treasury.receipt")).toEqual([]);
  });
});
