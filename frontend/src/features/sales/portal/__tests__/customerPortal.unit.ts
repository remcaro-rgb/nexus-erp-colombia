import { describe, expect, it } from "vitest";
import registry from "../../../../../../docs/spec/ui/module-registry.json";
import type { CustomerOpenItem } from "../api";
import { customerPortalApi } from "../api";
import { INTERNAL_KEYS, canAccept, paymentIntent, requestBody, sumMoney, visibleColumns } from "../model";
import descriptor from "../portal.descriptor";

const ID = "01a1c810-cba4-7198-bf46-81916377598f";
const open = (id: string, amount: string, currency = "COP"): CustomerOpenItem => ({
  document: { type: "sales.invoice", id, number: `SETT${id.slice(0, 3)}` },
  due_date: "2026-12-09",
  balance: { amount, currency },
});

describe("customer portal (F-PORTAL-CUS)", () => {
  it("contributes a page for every clientes menu except the shell dashboard", () => {
    const portal = (registry as { portals: Array<{ id: string; menus: Array<{ id: string }> }> }).portals.find((p) => p.id === "clientes");
    const menus = (portal?.menus ?? []).map((m) => m.id).filter((id) => id !== "inicio");
    expect(descriptor.portal).toBe("clientes");
    expect(Object.keys(descriptor.pages).sort()).toEqual([...menus].sort());
  });

  it("T-PORTAL-CUS-02 payment intent: amount > 0, ≤ open balance, exact decimals, one currency", () => {
    const items = [open(ID, "2380.0000"), open("11111111-1111-4111-8111-111111111111", "1190.0000")];
    expect(sumMoney(items.map((i) => i.balance.amount))).toBe("3570.00");
    expect(sumMoney(["0.1", "0.2"])).toBe("0.30");
    expect(paymentIntent([], "10").error).toBe("Seleccione al menos una factura.");
    expect(paymentIntent(items, "0").error).toMatch(/mayor que cero/);
    expect(paymentIntent(items, "12,5").error).toMatch(/mayor que cero/);
    expect(paymentIntent(items, "3570.01").error).toMatch(/supera el saldo/);
    expect(paymentIntent([open(ID, "1"), open("x", "1", "USD")], "1").error).toMatch(/misma moneda/);
    const ok = paymentIntent(items, "3570.00");
    expect(ok.error).toBeNull();
    expect(ok.body).toEqual({ documents: [ID, "11111111-1111-4111-8111-111111111111"], amount: { amount: "3570.00", currency: "COP" } });
  });

  it("T-PORTAL-CUS-03 quote requests carry items and quantities only (never prices)", () => {
    expect(requestBody("QUOTE", "", [{ item_id: "", quantity: "" }], {}).error).toBe("Agregue al menos un producto.");
    expect(requestBody("QUOTE", "", [{ item_id: "abc", quantity: "1" }], {}).error).toBe("Código de producto inválido.");
    expect(requestBody("QUOTE", "", [{ item_id: ID, quantity: "0" }], {}).error).toMatch(/mayor que cero/);
    const q = requestBody("QUOTE", " 5 unidades ", [{ item_id: ID, quantity: "5" }], {});
    expect(q.body).toEqual({ request_type: "QUOTE", message: "5 unidades", items: [{ item_id: ID, quantity: "5" }] });
    expect(JSON.stringify(q.body)).not.toMatch(/price/);
    const pc = requestBody("PROFILE_CHANGE", "", [], { phone: "6015550101", credit_limit: "1" });
    expect(pc.body).toEqual({ request_type: "PROFILE_CHANGE", message: "", changes: { phone: "6015550101" } });
    expect(requestBody("GENERAL", " ", [], {}).error).toBe("Escriba su consulta.");
    expect(canAccept("SENT")).toBe(true);
    expect(canAccept("ORDERED")).toBe(false);
  });

  it("T-PORTAL-CUS-04 the UI never renders internal keys and only calls the customer portal API", async () => {
    const row = { id: "1", number: "SETT1", total: null, cost_total: "1", margin: "2", price_snapshot: {}, formula_id: "f" };
    expect(visibleColumns(row)).toEqual(["id", "number", "total"]);
    expect(INTERNAL_KEYS).toContain("cost");
    const calls: string[] = [];
    const fake = {
      get: async <T,>(path: string) => {
        calls.push(path);
        return { items: [], next_cursor: null } as T;
      },
      post: async <T,>(path: string) => {
        calls.push(path);
        return {} as T;
      },
      patch: async <T,>(path: string) => {
        calls.push(path);
        return {} as T;
      },
    };
    const api = customerPortalApi(fake);
    await api.invoices();
    await api.statement("2026-01-01", "2026-11-09");
    await api.createIntent([ID], { amount: "1.00", currency: "COP" });
    expect(calls.every((c) => c.startsWith("/api/portal/v1/customer/"))).toBe(true);
  });

  it("T-PORTAL-CUS-05 fiscal downloads point to the synthetic PDF/XML endpoints", () => {
    const api = customerPortalApi({ get: async () => ({}) as never, post: async () => ({}) as never, patch: async () => ({}) as never });
    expect(api.invoicePdfUrl(ID)).toBe(`/api/portal/v1/customer/invoices/${ID}/pdf`);
    expect(api.invoiceXmlUrl(ID)).toBe(`/api/portal/v1/customer/invoices/${ID}/xml`);
    expect(api.notePdfUrl(ID)).toBe(`/api/portal/v1/customer/credit-notes/${ID}/pdf`);
    expect(api.statementUrl("2026-01-01", "2026-11-09", "csv")).toBe("/api/portal/v1/customer/statement?from=2026-01-01&to=2026-11-09&format=csv");
  });
});
