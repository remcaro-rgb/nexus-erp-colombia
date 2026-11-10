import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { comprasRegistry } from "../module";

describe("compras descriptor", () => {
  const entry = (registry as { modules: Array<Record<string, unknown>> }).modules.find((m) => m.id === "compras");

  it("conforms to module-registry.json (registry conformance)", () => {
    expect(comprasRegistry).toEqual(entry);
  });

  it("plugs a page into every F-PUR-CYCLE tab it owns, under its own submenu ids", () => {
    const tabs = comprasRegistry.submenus
      .flatMap((s) => ("tabs" in s ? s.tabs.map((t) => ({ s: s.id, t })) : []))
      .filter(({ t }) => t.feature === "F-PUR-CYCLE")
      .map(({ s, t }) => `${s}/${t.id}`);
    expect(tabs).toEqual(["cotizaciones/rfq", "cotizaciones/recibidas", "cotizaciones/comparacion", "ordenes/ordenes", "ordenes/recepciones"]);
    for (const key of tabs) expect(descriptor.pages?.[key]).toBeTruthy();
    expect(descriptor.id).toBe(comprasRegistry.id);
  });
});
