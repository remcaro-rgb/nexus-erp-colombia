import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { ventasRegistry } from "../module";

describe("ventas descriptor", () => {
  const entry = (registry as { modules: Array<Record<string, unknown>> }).modules.find((m) => m.id === "ventas");

  it("conforms to module-registry.json (registry conformance)", () => {
    expect(ventasRegistry).toEqual(entry);
  });

  it("plugs a page into every submenu/tab it owns (F-SAL-PRICING formulas tab included)", () => {
    const keys = ventasRegistry.submenus.flatMap((s) =>
      "tabs" in s && s.tabs ? s.tabs.map((t) => `${s.id}/${t.id}`) : s.id === "resumen" ? [] : [s.id],
    );
    expect(keys).toEqual(["oportunidades", "clientes/clientes", "clientes/formulas-precio", "pedidos", "facturas", "cartera"]);
    for (const key of ["oportunidades", "clientes/lista", "clientes/formulas-precio", "pedidos", "facturas", "cartera"]) expect(descriptor.pages?.[key]).toBeTruthy();
    const pricingTab = ventasRegistry.submenus.flatMap((s) => ("tabs" in s && s.tabs ? s.tabs : [])).find((t) => t.feature === "F-SAL-PRICING");
    expect(pricingTab?.id).toBe("formulas-precio");
    expect(descriptor.id).toBe(ventasRegistry.id);
  });
});
