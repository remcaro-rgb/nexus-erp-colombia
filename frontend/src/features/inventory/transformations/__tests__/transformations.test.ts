import { describe, expect, it } from "vitest";
import { transformationPages } from "../index";
import { quantityBalance, transformationActions, TYPE_TABS, yieldCheck } from "../model";

describe("transformations (F-INV-XFORM) helpers", () => {
  it("balances inputs against outputs (actual weight wins) plus waste", () => {
    const b = quantityBalance([
      { line_no: 1, role: "INPUT", quantity: "10" },
      { line_no: 2, role: "OUTPUT", quantity: "6", actual_weight: "6.2" },
      { line_no: 3, role: "OUTPUT", quantity: "3.3" },
      { line_no: 4, role: "WASTE", quantity: "0.5" },
    ]);
    expect(b).toEqual({ input: "10", output: "9.5", waste: "0.5", difference: "0", balanced: true });
    const off = quantityBalance([{ line_no: 1, role: "INPUT", quantity: "1" }, { line_no: 2, role: "OUTPUT", quantity: "0.999999" }]);
    expect(off.balanced).toBe(false);
    expect(off.difference).toBe("0.000001");
  });

  it("checks yield against expected ± tolerance without floats", () => {
    expect(yieldCheck("6.2", "10", "60", "2")).toEqual({ actual: "62", within: true });
    expect(yieldCheck("6.21", "10", "60", "2").within).toBe(false);
    expect(yieldCheck("1", "3", "33.333333", "0").actual).toBe("33.333333");
    expect(yieldCheck("1", "0", "50", "2")).toEqual({ actual: "0", within: false });
  });

  it("offers actions per status and reservation", () => {
    expect(transformationActions("DRAFT", false)).toEqual(["reserve", "submit", "post", "cancel"]);
    expect(transformationActions("DRAFT", true)).not.toContain("reserve");
    expect(transformationActions("SUBMITTED", false)).toEqual(["approve", "reject", "cancel"]);
    expect(transformationActions("POSTED", false)).toEqual(["reverse"]);
    expect(transformationActions("REVERSED", false)).toEqual([]);
  });

  it("lists the four transformation types and contributes two pages", () => {
    expect(TYPE_TABS.map((t) => t.type)).toEqual(["PRESENTATION", "PORTIONING", "MANUAL", "PRODUCTION"]);
    expect(Object.keys(transformationPages)).toEqual(["produccion/transformaciones", "produccion/plantillas-porcionado"]);
  });
});
