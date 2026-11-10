import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { activosModule } from "../module";

describe("activos descriptor", () => {
  const entry = (registry as unknown as { modules: Record<string, unknown>[] }).modules.find((m) => m.id === "activos")!;

  it("conforms to module-registry.json (registry conformance)", () => {
    for (const key of ["id", "label", "icon", "group", "owner_task", "route", "permission", "submenus", "quick_action"]) {
      expect(activosModule[key as keyof typeof activosModule]).toEqual(entry[key]);
    }
    const dash = entry.dashboard as Record<string, unknown>;
    expect(activosModule.dashboard.kpis).toEqual(dash.kpis);
    expect(activosModule.dashboard.panels).toEqual(dash.panels);
    expect(activosModule.dashboard.empty_state).toEqual(dash.empty_state);
  });

  it("provides a page for every non-dashboard submenu and a real quick-action route", () => {
    const subs = activosModule.submenus.filter((s) => s.id !== "resumen").map((s) => s.id);
    expect(Object.keys(descriptor.pages ?? {}).sort()).toEqual([...subs].sort());
    expect(activosModule.quick_action.action.slice("route:".length).startsWith("/m/activos/registro")).toBe(true);
    for (const k of activosModule.dashboard.kpis) expect(k.drill.startsWith("/m/activos/")).toBe(true);
  });
});
