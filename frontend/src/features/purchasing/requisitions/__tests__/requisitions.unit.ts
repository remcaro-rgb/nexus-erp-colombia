import { describe, expect, it } from "vitest";
import registry from "../../../../../../docs/spec/ui/module-registry.json";
import type { ConsolidationPreviewLine, RequisitionDto } from "../api";
import { addDecimals, canDecide, lineageBalanced, sourcingSummary, toRequest, validateDraft } from "../model";
import { requisitionPages } from "../pages";

describe("Compras › Solicitudes internas (F-INV-REQ)", () => {
  it("provides a page for every F-INV-REQ tab of the compras registry entry", () => {
    const compras = (registry as { modules: Array<{ id: string; submenus: Array<{ id: string; tabs?: Array<{ id: string; feature?: string }> }> }> }).modules.find((m) => m.id === "compras");
    const keys = (compras?.submenus ?? []).flatMap((s) => (s.tabs ?? []).filter((t) => t.feature === "F-INV-REQ").map((t) => `${s.id}/${t.id}`));
    expect(keys).toEqual(["solicitudes/mis-solicitudes", "solicitudes/por-aprobar", "solicitudes/consolidacion", "solicitudes/abastecimiento"]);
    for (const key of [...keys, "solicitudes"]) expect(requisitionPages[key]).toBeTruthy();
  });

  it("validates the request form in es-CO and builds the contract body", () => {
    const form = { warehouseId: "", neededBy: "2026-11-01", priority: "NORMAL" as const, notes: "", lines: [{ itemId: "", qty: "1.1234567", uom: "UND" }] };
    const errs = validateDraft(form, "2026-11-09");
    expect(Object.keys(errs).sort()).toEqual(["lines.0.itemId", "lines.0.qty", "neededBy", "warehouseId"]);
    const ok = { ...form, warehouseId: "w", neededBy: "2026-11-13", lines: [{ itemId: "i", qty: "5", uom: "UND" }] };
    expect(validateDraft(ok, "2026-11-09")).toEqual({});
    expect(toRequest(ok, "2026-11-09").lines).toEqual([{ line_no: 1, item_id: "i", quantity: { value: "5", uom: "UND" } }]);
  });

  it("sums decimal strings exactly and checks the D-031 lineage balance", () => {
    expect(addDecimals(["0.1", "0.2", "36.000000"])).toBe("36.3");
    const line = { quantity: "26.000000", sources: [{ qty: "6.000000" }, { qty: "12.000000" }, { qty: "8.000000" }] } as unknown as ConsolidationPreviewLine;
    expect(lineageBalanced(line)).toBe(true);
    expect(lineageBalanced({ ...line, quantity: "25" })).toBe(false);
  });

  it("summarises the transfer / purchase split and enforces SoD in the inbox", () => {
    const req = {
      requisition_status: "SUBMITTED",
      requester_id: "u1",
      lines: [
        { qty_transfer: "36.000000", qty_procure: "14.000000", fulfilled_qty: "0" },
        { qty_transfer: "1", qty_procure: "0", fulfilled_qty: "1" },
      ],
    } as unknown as RequisitionDto;
    expect(sourcingSummary(req)).toEqual({ transfer: "37", purchase: "14", fulfilled: "1" });
    expect(canDecide(req, "u1")).toBe(false);
    expect(canDecide(req, "u2")).toBe(true);
  });
});
