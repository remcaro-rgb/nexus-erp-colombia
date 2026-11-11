import { describe, expect, it } from "vitest";
import registry from "../../../../../docs/spec/ui/module-registry.json";
import { exportUrl, periodEnd, problemText, reportRows, validateAgreement, validateRun } from "../api";
import descriptor, { intercompanySubmenu } from "../module";

const M = { required: "req", uuid: "uuid", same: "same", period: "period" };
const A = "0190b0b0-0000-7000-8000-000000000001";
const B = "0190b0b0-0000-7000-8000-000000000002";

describe("P16b intercompany/consolidation UI", () => {
  it("contributes exactly the registry's 'intercompania' submenu of 'tesoreria'", () => {
    const treasury = (registry as { modules: Array<{ id: string; submenus: Array<Record<string, string>> }> }).modules.find((m) => m.id === "tesoreria");
    const sub = treasury?.submenus.find((s) => s.id === "intercompania");
    expect(sub).toBeDefined();
    expect({ id: sub!.id, label: sub!.label, route: sub!.route, permission: sub!.permission }).toEqual({ ...intercompanySubmenu });
    expect(descriptor.contributions?.[0]?.module).toBe("tesoreria");
    expect(descriptor.pages).toBeUndefined();
  });

  it("validates agreements and runs before calling the API", () => {
    expect(validateAgreement({ code: "", name: "x", company_a_id: A, company_b_id: A }, M)).toEqual({ code: "req", company_b_id: "same" });
    expect(validateAgreement({ code: "AB", name: "x", company_a_id: "nope", company_b_id: B }, M)).toEqual({ company_a_id: "uuid" });
    expect(validateAgreement({ code: "AB", name: "x", company_a_id: A, company_b_id: B }, M)).toEqual({});
    expect(validateRun({ group_id: "", period: "2026-13", document_date: "" }, M)).toEqual({ group_id: "req", period: "period", document_date: "req" });
  });

  it("derives the period end, export URLs and grid rows", () => {
    expect(periodEnd("2028-02")).toBe("2028-02-29");
    expect(periodEnd("2026-11")).toBe("2026-11-30");
    expect(periodEnd("bad")).toBe("");
    expect(exportUrl("/api/v1/consolidation/reports/balance", { run_id: undefined })).toBe("/api/v1/consolidation/reports/balance?format=csv");
    expect(exportUrl("/x", { run_id: "r1" })).toBe("/x?format=csv&run_id=r1");
    const rows = reportRows({ report: "r", columns: [], rows: [{ account: "111005" }, { account: "111005" }] });
    expect(rows.map((r) => r.id)).toEqual(["111005#0", "111005#1"]);
    expect(reportRows(null)).toEqual([]);
  });

  it("maps server error codes to Spanish messages and never invents one", () => {
    expect(problemText("sod_violation", "x")).toMatch(/Segregación/);
    expect(problemText("unknown_code", "fallback")).toBe("fallback");
    expect(problemText(undefined, "fallback")).toBe("fallback");
  });
});
