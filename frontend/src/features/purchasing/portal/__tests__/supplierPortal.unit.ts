import { describe, expect, it } from "vitest";
import registry from "../../../../../../docs/spec/ui/module-registry.json";
import type { SupplierRfq } from "../api";
import { approvalsNeeded, canQuote, quoteLines, stripDataUrl } from "../model";
import descriptor from "../portal.descriptor";

describe("supplier portal (F-PORTAL-SUP)", () => {
  it("contributes a page for every proveedores menu except the shell dashboard", () => {
    const portal = (registry as { portals: Array<{ id: string; menus: Array<{ id: string }> }> }).portals.find((p) => p.id === "proveedores");
    const menus = (portal?.menus ?? []).map((m) => m.id).filter((id) => id !== "inicio");
    expect(descriptor.portal).toBe("proveedores");
    expect(Object.keys(descriptor.pages).sort()).toEqual([...menus].sort());
  });

  it("builds quote lines from the RFQ and validates prices", () => {
    const rfq = { id: "r", currency: "COP", rfq_status: "OPEN", lines: [{ line_no: 1, quantity: { value: "10", uom: "UND" } }, { line_no: 2, quantity: { value: "5", uom: "UND" } }] } as unknown as SupplierRfq;
    const bad = quoteLines(rfq, { 1: "2800", 2: "12,5" });
    expect(bad.errors).toEqual({ 2: "Precio unitario en pesos, máximo 4 decimales." });
    const ok = quoteLines(rfq, { 1: "2800", 2: "12.5" });
    expect(ok.errors).toEqual({});
    expect(ok.lines[0]).toEqual({ line_no: 1, quantity: { value: "10", uom: "UND" }, unit_price: { amount: "2800", currency: "COP" } });
    expect(canQuote(rfq)).toBe(true);
    expect(canQuote({ ...rfq, rfq_status: "CLOSED" })).toBe(false);
  });

  it("states that a bank change needs two approvals and strips data URLs", () => {
    expect(approvalsNeeded({ bank_account: "123456" })).toBe(2);
    expect(approvalsNeeded({ contacts: {} })).toBe(1);
    expect(stripDataUrl("data:text/xml;base64,PHg+")).toBe("PHg+");
  });
});
