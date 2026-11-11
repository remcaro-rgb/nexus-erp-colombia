import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import descriptor, { datosModule } from "../module";
import { fromBase64, problemText, toBase64 } from "../api";

describe("datos descriptor", () => {
  const entry = (registry as unknown as { modules: Record<string, unknown>[] }).modules.find((m) => m.id === "datos")!;

  it("conforms to module-registry.json (registry conformance)", () => {
    for (const key of ["id", "label", "icon", "group", "owner_task", "route", "permission", "submenus", "quick_action"]) {
      expect(datosModule[key as keyof typeof datosModule]).toEqual(entry[key]);
    }
    const dash = entry.dashboard as Record<string, unknown>;
    expect(datosModule.dashboard.kpis).toEqual(dash.kpis);
    expect(datosModule.dashboard.panels).toEqual(dash.panels);
    expect(datosModule.dashboard.empty_state).toEqual(dash.empty_state);
    expect(datosModule.dashboard.kpi_endpoint).toBe("/api/v1/kpis/datos");
  });

  it("provides a page for every non-dashboard submenu and real drill/quick-action routes", () => {
    const subs = datosModule.submenus.filter((s) => s.id !== "resumen").map((s) => s.id);
    expect(Object.keys(descriptor.pages ?? {}).sort()).toEqual([...subs].sort());
    expect(datosModule.quick_action.action).toBe("route:/m/datos/importar/nueva");
    for (const k of datosModule.dashboard.kpis) expect(k.drill.startsWith("/m/datos/")).toBe(true);
  });
});

describe("dataio api helpers", () => {
  it("base64 round trip of binary content (XLSX/ZIP uploads)", () => {
    const bytes = new Uint8Array([0x50, 0x4b, 3, 4, 0, 255, 128, 10]);
    expect(Array.from(fromBase64(toBase64(bytes.buffer)))).toEqual(Array.from(bytes));
  });

  it("maps stable dataio error codes to es-CO text", () => {
    expect(problemText("import_validation_failed", "x")).toMatch(/no se aplicó ninguna fila/);
    expect(problemText("otro", "fallback")).toBe("fallback");
  });
});
