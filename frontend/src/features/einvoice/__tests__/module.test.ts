import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { facturacionModule } from "../module";

describe("facturacion descriptor (P15)", () => {
  const entry = (registry as { modules: Record<string, unknown>[] }).modules.find((m) => m.id === "facturacion")!;

  it("conforms to module-registry.json (registry conformance)", () => {
    for (const key of ["id", "label", "icon", "group", "owner_task", "route", "permission", "submenus", "quick_action"]) {
      expect(facturacionModule[key as keyof typeof facturacionModule]).toEqual(entry[key]);
    }
  });

  it("contributes a page for every non-dashboard submenu and the F-PT-OPEN tab", () => {
    expect(descriptor.id).toBe("facturacion");
    expect(Object.keys(descriptor.pages ?? {}).sort()).toEqual(["documentos", "numeracion", "numeracion/proveedores-tecnologicos", "rechazos"]);
    const tab = facturacionModule.submenus.find((s) => s.id === "numeracion")?.tabs?.[0];
    expect(tab?.feature).toBe("F-PT-OPEN");
    expect(tab?.permission).toBe("einvoice.provider.read");
  });

  it("drill links of the registry dashboard resolve to the module's real list routes", () => {
    const dash = entry.dashboard as { kpis: { drill: string }[]; panels: { drill: string }[] };
    const routes = facturacionModule.submenus.map((s) => s.route);
    for (const drill of [...dash.kpis, ...dash.panels].map((k) => k.drill.split("?")[0])) {
      expect(routes).toContain(drill);
    }
  });
});
