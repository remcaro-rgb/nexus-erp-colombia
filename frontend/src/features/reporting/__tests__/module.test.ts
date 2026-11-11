import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { reportesRegistry } from "../module";

describe("reportes descriptor", () => {
  const modules = (registry as { modules: Array<Record<string, unknown>> }).modules;
  const entry = modules.find((m) => m.id === "reportes");

  it("conforms to module-registry.json (registry conformance)", () => {
    expect(reportesRegistry).toEqual(entry);
  });

  it("plugs P17a pages under the registry submenu ids and contributes the P17 submenus of inicio", () => {
    const ids = reportesRegistry.submenus.map((s) => s.id);
    for (const key of ["explorador", "programados", "documentos"]) {
      expect(ids).toContain(key);
      expect(descriptor.pages?.[key]).toBeTruthy();
    }
    const inicio = modules.find((m) => m.id === "inicio") as { submenus: Array<{ id: string; owner_task?: string }> };
    const owned = inicio.submenus.filter((s) => s.owner_task === "P17").map((s) => s.id);
    expect(descriptor.contributions?.map((c) => c.submenu.id)).toEqual(owned);
    for (const c of descriptor.contributions ?? []) {
      expect(inicio.submenus.find((s) => s.id === c.submenu.id)).toEqual(c.submenu);
    }
    expect(descriptor.id).toBe("reportes");
  });
});
