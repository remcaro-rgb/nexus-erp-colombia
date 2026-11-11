import { describe, expect, it } from "vitest";
import { LABEL_FIELDS, bars, gtinCheckDigit, isRestrictedCirculation, isValidGtin, labelLines, normalizeScan } from "../model";

describe("barcode model (client mirror of the server encoders)", () => {
  it("T-INV-BARCODE-01 computes GS1 mod-10 check digits and validates GTINs", () => {
    expect(gtinCheckDigit("400638133393")).toBe(1);
    expect(gtinCheckDigit("590123412345")).toBe(7);
    expect(gtinCheckDigit("290000000001")).toBe(8);
    for (const v of ["4006381333931", "036000291452", "00012345678905", "96385074"]) expect(isValidGtin(v)).toBe(true);
    for (const v of ["4006381333932", "1234567", "ABCDEFGHIJKLM"]) expect(isValidGtin(v)).toBe(false);
    expect(isRestrictedCirculation("2900000000018")).toBe(true);
    expect(isRestrictedCirculation("4006381333931")).toBe(false);
  });

  it("normalizes keyboard-wedge scans and turns modules into bars", () => {
    expect(normalizeScan("  ALFA-00000001\r\n")).toBe("ALFA-00000001");
    expect(bars("1101", 0)).toEqual([{ x: 0, width: 2 }, { x: 3, width: 1 }]);
  });

  it("label lines follow fixed fields only (no cost field exists)", () => {
    expect(LABEL_FIELDS).not.toContain("cost");
    expect(labelLines({ value: "ALFA-1", name: "Tornillo", cost: "98765" }, ["value", "cost", "name"])).toEqual(["ALFA-1", "Tornillo"]);
  });
});
