import { describe, expect, it } from "vitest";

import { TAX_SUBMENU, registerTaxContribution, visibleTaxSubmenu, type ContributedSubmenu } from "../contribution";
import { blockingItems, buildTimelines, displayValue, explain, formatDecimal, type Parameter } from "../model";

function param(over: Partial<Parameter>): Parameter {
  return {
    id: crypto.randomUUID(),
    code: "uvt",
    value: { type: "decimal", value: "52374" },
    valid_from: "2026-01-01",
    valid_to: "2026-12-31",
    source_id: "s",
    parameter_status: "VERIFIED-PRIMARY",
    legal_reviewed_by: null,
    legal_reviewed_at: null,
    synthetic: false,
    approval: "APPROVED",
    proposed_by: null,
    legal_history: [],
    notes: "",
    version: 1,
    ...over,
  };
}

describe("P06 tax contribution", () => {
  it("appears only with tax.read and filters tabs by permission", () => {
    expect(visibleTaxSubmenu(new Set(["tax.parameter.read"]))).toBeNull();
    const visible = visibleTaxSubmenu(new Set(["tax.read", "tax.parameter.read"]));
    expect(visible?.route).toBe("/m/contabilidad/impuestos");
    expect(visible?.tabs.map((t) => t.id)).toEqual(["parametros"]);
    const seen: ContributedSubmenu[] = [];
    registerTaxContribution({ contributeSubmenu: (s) => seen.push(s) });
    expect(seen).toEqual([TAX_SUBMENU]);
    expect(TAX_SUBMENU.tabs.some((t) => t.id === "exogena")).toBe(false); // owned by P17
  });
});

describe("P06 parameter timelines", () => {
  it("marks UNKNOWN and missing values as blocking (fail closed)", () => {
    const params = [
      param({}),
      param({ code: "retefuente.compras.tarifa", value: { type: "unknown" }, parameter_status: "UNKNOWN" }),
      param({ code: "smmlv", parameter_status: "DISPUTED", value: { type: "decimal", value: "1750905" } }),
    ];
    const timelines = buildTimelines(params, "2026-07-01");
    expect(blockingItems(timelines)).toEqual(["retefuente.compras.tarifa"]);
    expect(timelines.find((t) => t.code === "smmlv")?.disputed).toBe(true);
    expect(blockingItems(buildTimelines(params, "2027-01-15"))).toEqual(["retefuente.compras.tarifa", "smmlv", "uvt"]);
  });

  it("ignores proposed values and shows stored values without hard-coded rates", () => {
    const proposed = param({ approval: "PROPOSED", valid_from: "2027-01-01", valid_to: null });
    expect(blockingItems(buildTimelines([proposed], "2027-02-01"))).toEqual(["uvt"]);
    expect(displayValue(param({ value: { type: "percent", value: "19" } }))).toBe("19 %");
    expect(displayValue(param({}))).toBe("52.374");
    expect(formatDecimal("1234567.5")).toBe("1.234.567,5");
  });

  it("explains one computed line from the engine trace", () => {
    const trace = [
      { step: "document", line_ref: null, tax_code: null, detail: "doc", data: {} },
      { step: "parameter", line_ref: "1", tax_code: "IVA19", detail: "tarifa", data: { status: "VERIFIED-PRIMARY" } },
      { step: "amount", line_ref: "2", tax_code: "IVA19", detail: "otra línea", data: {} },
    ];
    expect(explain(trace, "1", "IVA19").map((s) => s.step)).toEqual(["document", "parameter"]);
  });
});
