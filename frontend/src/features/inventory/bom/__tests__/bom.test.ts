import { describe, expect, it } from "vitest";
import type { BomDto, BomInput, ExplosionNodeDto } from "../api";
import { bomPages } from "../index";
import { bomActions, flattenExplosion, groupVersions, validateBom } from "../model";

function bom(code: string, version_no: number, over: Partial<BomDto> = {}): BomDto {
  return {
    id: `${code}-${version_no}`, code, name: code, bom_status: "DRAFT", bom_type: "PRODUCTION", item_id: "out",
    version_no, valid_from: "2026-01-01", valid_to: null, output_qty: "10", yield_percent: "100",
    components: { lines: [], byproducts: [] }, version: 1, ...over,
  };
}

function input(over: Partial<BomInput> = {}): BomInput {
  return {
    code: "R-PAN", name: "Pan", item_id: "out", valid_from: "2026-01-01", output_qty: "10", yield_percent: "80",
    components: { lines: [{ item_id: "flour", quantity: "4" }] }, ...over,
  };
}

describe("recipes (F-INV-BOM) helpers", () => {
  it("groups versions by code case-insensitively, newest first", () => {
    const groups = groupVersions([bom("R-B", 1), bom("r-a", 1), bom("R-A", 3), bom("R-A", 2)]);
    expect(groups.map((g) => g.code)).toEqual(["r-a", "R-B"]);
    expect(groups[0]!.versions.map((v) => v.version_no)).toEqual([3, 2, 1]);
  });

  it("offers actions per status", () => {
    expect(bomActions("DRAFT")).toContain("activate");
    expect(bomActions("ACTIVE")).not.toContain("activate");
    expect(bomActions("OBSOLETE")).toEqual(["copy-version"]);
  });

  it("validates yield, quantities, self reference and validity range", () => {
    expect(validateBom(input())).toEqual([]);
    expect(validateBom(input({ yield_percent: "0" }))).toHaveLength(1);
    expect(validateBom(input({ yield_percent: "100.5" }))).toHaveLength(1);
    expect(validateBom(input({ output_qty: "0" }))).toHaveLength(1);
    expect(validateBom(input({ components: { lines: [] } }))).toHaveLength(1);
    expect(validateBom(input({ components: { lines: [{ item_id: "out", quantity: "1" }] } }))[0]).toMatch(/sí mismo/);
    expect(validateBom(input({ valid_to: "2025-12-31" }))).toHaveLength(1);
  });

  it("flattens a multi-level explosion in depth-first order", () => {
    const leaf = (code: string, level: number, quantity: string, children: ExplosionNodeDto[] = []): ExplosionNodeDto => ({
      item_id: code, item_code: code, level, quantity, is_byproduct: false, children,
    });
    const tree = leaf("PAN", 0, "20", [leaf("HARINA", 1, "10.500000"), leaf("MIX", 1, "6", [leaf("AGUA", 2, "2.000000")])]);
    expect(flattenExplosion(tree)).toEqual([
      { depth: 1, code: "HARINA", quantity: "10.5", byproduct: false },
      { depth: 1, code: "MIX", quantity: "6", byproduct: false },
      { depth: 2, code: "AGUA", quantity: "2", byproduct: false },
    ]);
  });

  it("contributes the recetas page", () => {
    expect(Object.keys(bomPages)).toEqual(["produccion/recetas"]);
  });
});
