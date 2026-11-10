import { describe, expect, it } from "vitest";
import type { AvailabilityDto, LocationNodeDto } from "../api";
import { flattenTree, formatQty, fromScaled, reconciles, toScaled, totalsByPresentation, transferActions, validateMove } from "../model";
import inventoryModule from "../module";

function row(over: Partial<AvailabilityDto>): AvailabilityDto {
  return {
    item_id: "i", warehouse_id: "w", location_id: "l", location_code: "ALMACEN", location_type: "INTERNAL", lot_id: null,
    on_hand: "68.000000", reserved: "24.000000", available: "44.000000", packaged_base: "60.000000", loose: "8.000000",
    packaged: [{ presentation_id: "box", code: "CJ12", factor: "12.000000", packages: "5.000000", reserved_packages: "2.000000", available_packages: "3.000000" }],
    ...over,
  };
}

describe("inventory model (decimal-safe, D-013)", () => {
  it("scales decimals exactly without floats", () => {
    expect(toScaled("0.1") + toScaled("0.2")).toBe(toScaled("0.3"));
    expect(fromScaled(toScaled("12.500000"))).toBe("12.5");
    expect(formatQty("1234567.250000")).toBe("1.234.567,25");
    expect(() => toScaled("1e3")).toThrow();
  });

  it("totals per presentation keep packaged and loose apart (never double counted)", () => {
    const t = totalsByPresentation([row({}), row({ location_id: "l2", on_hand: "36.000000", packaged_base: "36.000000", loose: "0.000000",
      packaged: [{ presentation_id: "box", code: "CJ12", factor: "12.000000", packages: "3.000000", reserved_packages: "0.000000", available_packages: "3.000000" }] })]);
    expect(t.presentations).toEqual([{ presentation_id: "box", code: "CJ12", factor: "12", packages: "8", available_packages: "6", base: "96" }]);
    expect(t.loose).toBe("8");
    expect(t.canonical).toBe("104");
    expect(toScaled(t.presentations[0]!.base) + toScaled(t.loose)).toBe(toScaled(t.canonical));
    expect(reconciles(row({}))).toBe(true);
    expect(reconciles(row({ loose: "9.000000" }))).toBe(false);
  });

  it("flattens the bin tree and marks only active leaf non-VIEW bins as postable", () => {
    const leaf = (code: string, type: LocationNodeDto["location_type"] = "INTERNAL"): LocationNodeDto => ({
      id: code, code, name: code, location_type: type, status: "ACTIVE", path: `WH/${code}`, parent_id: "WH", on_hand: "0", is_leaf: true, children: [],
    });
    const root: LocationNodeDto = { ...leaf("WH", "VIEW"), path: "WH", parent_id: null, is_leaf: false, children: [leaf("RECEPCION"), leaf("TRANSITO", "TRANSIT")] };
    const flat = flattenTree([root]);
    expect(flat.map((r) => [r.node.code, r.depth, r.postable])).toEqual([["WH", 0, false], ["RECEPCION", 1, true], ["TRANSITO", 1, true]]);
  });

  it("offers only the valid transfer transitions and validates moves", () => {
    expect(transferActions("DRAFT")).toEqual(["submit", "cancel"]);
    expect(transferActions("SHIPPED")).toEqual(["receive"]);
    expect(transferActions("RECEIVED")).toEqual([]);
    expect(validateMove({ item_id: "i", from_location_id: "a", to_location_id: "a", quantity: "1" })).toMatch(/distintos/);
    expect(validateMove({ item_id: "i", from_location_id: "a", to_location_id: "b", quantity: "0" })).toMatch(/mayor/);
    expect(validateMove({ item_id: "i", from_location_id: "a", to_location_id: "b", quantity: "2.5" })).toBeNull();
  });

  it("descriptor contributes the F-INV-LOC / F-INV-PRES pages of the registry", () => {
    expect(inventoryModule.id).toBe("inventario");
    expect(Object.keys(inventoryModule.pages ?? {}).sort()).toEqual([
      "existencias/por-presentacion", "existencias/por-ubicacion", "maestros/bodegas-ubicaciones", "maestros/presentaciones",
      "movimientos/entre-ubicaciones", "movimientos/traslados",
    ]);
  });
});
