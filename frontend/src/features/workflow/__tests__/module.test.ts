import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import { aprobacionesModule } from "../module";
import { describeErrors } from "../formula/format";

describe("aprobaciones descriptor", () => {
  const entry = (registry as { modules: Record<string, unknown>[] }).modules.find((m) => m.id === "aprobaciones")!;

  it("conforms to module-registry.json (registry conformance)", () => {
    for (const key of ["id", "label", "icon", "group", "owner_task", "route", "permission", "submenus", "quick_action"]) {
      expect(aprobacionesModule[key as keyof typeof aprobacionesModule]).toEqual(entry[key]);
    }
    const dash = entry.dashboard as Record<string, unknown>;
    expect(aprobacionesModule.dashboard.kpis).toEqual(dash.kpis);
    expect(aprobacionesModule.dashboard.panels).toEqual(dash.panels);
    expect(aprobacionesModule.dashboard.empty_state).toEqual(dash.empty_state);
  });

  it("formats positioned formula errors in es-CO", () => {
    const lines = describeErrors({
      ok: false,
      errors: [{ code: "formula_syntax_error", detail: "attribute access is not allowed", position: 5, line: 1, column: 6 }],
      tests: [],
      order: [],
      hash: null,
    });
    expect(lines[0]).toContain("línea 1, columna 6");
  });
});
