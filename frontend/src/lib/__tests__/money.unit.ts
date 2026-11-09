// T-P08-04 MoneyInput / money helpers never use floats: 0.1 + 0.2 cases, 999.999.999.999,9999, es-CO formats.
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { decimalEquals, formatCOP, formatCOPCompact, formatEsCoDecimal, parseEsCoDecimal, roundCurrency, sumDecimals, DecimalInputError } from "../money";

describe("T-P08-04 decimal-safe money", () => {
  it("T-P08-04 0.1 + 0.2 is exactly 0.3", () => {
    expect(0.1 + 0.2).not.toBe(0.3); // the float trap this module avoids
    expect(sumDecimals(["0.1", "0.2"])).toBe("0.3");
    expect(decimalEquals(sumDecimals(["0.1", "0.2"]), "0.3")).toBe(true);
    expect(sumDecimals(Array.from({ length: 10 }, () => "0.1"))).toBe("1");
  });

  it("T-P08-04 parses es-CO input into canonical strings", () => {
    expect(parseEsCoDecimal("1.234.567,89")).toBe("1234567.89");
    expect(parseEsCoDecimal("$ 1.284.500")).toBe("1284500");
    expect(parseEsCoDecimal("-12,3")).toBe("-12.3");
    expect(parseEsCoDecimal("0,1")).toBe("0.1");
    expect(parseEsCoDecimal("-0")).toBe("0");
    expect(parseEsCoDecimal("999.999.999.999,9999")).toBe("999999999999.9999");
    expect(parseEsCoDecimal("9999999999999999,9999")).toBe("9999999999999999.9999"); // NUMERIC(20,4) max
    expect(() => parseEsCoDecimal("99999999999999999")).toThrow(DecimalInputError);
    expect(() => parseEsCoDecimal("1,23456")).toThrow(/Máximo 4 decimales/);
    expect(() => parseEsCoDecimal("1.23,5")).toThrow(DecimalInputError);
    expect(() => parseEsCoDecimal("1e5")).toThrow(DecimalInputError);
    expect(() => parseEsCoDecimal("")).toThrow(DecimalInputError);
    expect(parseEsCoDecimal("1,123456", "quantity")).toBe("1.123456");
  });

  it("T-P08-04 formats COP and large values without precision loss", () => {
    expect(formatCOP("1284500")).toBe("$ 1.284.500");
    expect(formatCOP("1234567.89", { decimals: 2 })).toBe("$ 1.234.567,89");
    expect(formatCOP("-5000")).toBe("-$ 5.000");
    expect(formatEsCoDecimal("999999999999.9999", { maxFraction: 4 })).toBe("999.999.999.999,9999");
    expect(formatEsCoDecimal("9999999999999999.9999", { maxFraction: 4 })).toBe("9.999.999.999.999.999,9999");
    expect(formatCOPCompact("1284500")).toBe("$ 1,3 M");
    expect(roundCurrency("0.125")).toBe("0.13"); // ROUND_HALF_UP (D-001)
    expect(roundCurrency("2.675")).toBe("2.68"); // float would give 2.67
  });

  it("T-P08-04 no parseFloat/Number() conversions of amounts in money code and inputs", () => {
    const files = ["src/lib/money.ts", "src/design-system/components/MoneyInput.tsx"];
    for (const f of files) {
      const src = readFileSync(join(process.cwd(), f), "utf-8").replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
      expect(src, f).not.toMatch(/parseFloat\(|Number\(|\.toNumber\(|\+\s*e\.target\.value/);
    }
    // No `type="number"` money inputs anywhere in the design system.
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
    for (const f of walk(join(process.cwd(), "src/design-system")).filter((x) => x.endsWith(".tsx") && !x.includes("__tests__"))) {
      expect(readFileSync(f, "utf-8"), f).not.toMatch(/type="number"/);
    }
  });
});
