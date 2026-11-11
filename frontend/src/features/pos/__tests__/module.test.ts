import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { posModule } from "../module";

describe("pos descriptor (P12)", () => {
  const entry = (registry as { modules: Record<string, unknown>[] }).modules.find((m) => m.id === "pos")!;

  it("conforms to module-registry.json (registry conformance)", () => {
    for (const key of ["id", "label", "icon", "group", "owner_task", "route", "permission", "submenus", "quick_action"]) {
      expect(posModule[key as keyof typeof posModule]).toEqual(entry[key]);
    }
  });

  it("contributes a page for every non-dashboard submenu", () => {
    expect(descriptor.id).toBe("pos");
    expect(Object.keys(descriptor.pages ?? {}).sort()).toEqual(["tiquetes", "turnos", "vender"]);
  });

  it("drill links of the registry dashboard resolve to the module's real list routes", () => {
    const dash = entry.dashboard as { kpis: { drill: string }[]; panels: { drill: string }[] };
    const routes = posModule.submenus.map((s) => s.route);
    for (const drill of [...dash.kpis, ...dash.panels].map((k) => k.drill.split("?")[0])) {
      expect(routes).toContain(drill);
    }
  });
});
