import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { tesoreriaRegistry } from "../module";

describe("tesoreria descriptor", () => {
  const entry = (registry as { modules: Array<Record<string, unknown>> }).modules.find((m) => m.id === "tesoreria");

  it("conforms to module-registry.json (registry conformance)", () => {
    expect(tesoreriaRegistry).toEqual(entry);
  });

  it("plugs a page into every F-TRE-RC / F-TRE-CE tab, under its own submenu ids", () => {
    const tabs = tesoreriaRegistry.submenus
      .flatMap((s) => ("tabs" in s ? s.tabs.map((t) => ({ s: s.id, t })) : []))
      .filter(({ t }) => t.feature === "F-TRE-RC" || t.feature === "F-TRE-CE")
      .map(({ s, t }) => `${s}/${t.id}`);
    expect(tabs).toEqual(["recibos/recibos", "recibos/conceptos", "egresos/comprobantes", "egresos/lotes"]);
    for (const key of tabs) expect(descriptor.pages?.[key]).toBeTruthy();
    expect(descriptor.id).toBe(tesoreriaRegistry.id);
  });
});
