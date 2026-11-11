import { describe, expect, it } from "vitest";
import type { ReportColumn, ReportResult, SemanticEntity } from "../api";
import { buildQuery, dimensionOptions, emptyBuilder, formatCell, formatDecimal, toCsv, totalsLine } from "../model";

const entity: SemanticEntity = {
  entity: "ledger.journal_line",
  label: "Movimientos",
  fields: [
    { key: "account_code", label: "Cuenta", type: "string", classification: "internal", hierarchy: null, time: false },
    { key: "cost_center", label: "Centro de costo", type: "uuid", classification: "internal", hierarchy: "cost_center", time: false },
  ],
  measures: [{ key: "net", label: "Neto", type: "money", aggregation: "sum", sensitive: false }],
  time_grains: ["month"],
  k_anonymity: null,
};

describe("report builder model", () => {
  it("builds the semantic query from the builder state (empty filters dropped)", () => {
    const state = { ...emptyBuilder("ledger.journal_line"), measures: ["net"], dimensions: ["cost_center:root"], asOf: "2026-10-31",
      filters: [{ field: "account_code", op: "eq" as const, value: "413505" }, { field: "account_code", op: "eq" as const, value: "" }] };
    expect(buildQuery(state)).toEqual({ entity: "ledger.journal_line", measures: ["net"], dimensions: ["cost_center:root"],
      filters: [{ field: "account_code", op: "eq", value: "413505" }], as_of: "2026-10-31" });
  });

  it("offers hierarchy groupings for hierarchical dimensions", () => {
    expect(dimensionOptions(entity).map((d) => d.value)).toEqual(["account_code", "cost_center", "cost_center:root", "cost_center:parent"]);
  });

  it("formats Decimal strings es-CO without floating point (half up)", () => {
    expect(formatDecimal("1234567.5")).toBe("1.234.567,50");
    expect(formatDecimal("-150000.0000")).toBe("-150.000,00");
    expect(formatDecimal("0.005")).toBe("0,01");
    expect(formatDecimal("999.995")).toBe("1.000,00");
    expect(formatDecimal("12345678901234567890.125")).toBe("12.345.678.901.234.567.890,13");
    expect(formatDecimal("-0.001")).toBe("0,00");
  });

  it("renders cells, suppressed groups, labels, totals and CSV", () => {
    const result: ReportResult = {
      report: "ledger.journal_line",
      columns: [{ key: "cost_center:root", label: "Grupo", type: "uuid" }, { key: "net", label: "Neto", type: "money" }],
      rows: [
        { "cost_center:root": "id-1", "cost_center:root__label": "ADMIN — Administración", net: "400000.0000" },
        { "cost_center:root": "id-2", net: null, suppressed: true },
      ],
      totals: { net: "400000.0000" },
      warnings: [],
      synthetic: true,
    };
    const [group, net] = result.columns as [ReportColumn, ReportColumn];
    const [first, hidden] = result.rows as [Record<string, unknown>, Record<string, unknown>];
    expect(formatCell(group, first)).toBe("ADMIN — Administración");
    expect(formatCell(net, first)).toBe("$ 400.000,00");
    expect(formatCell(net, hidden)).toBe("— (oculto, k < 5)");
    expect(totalsLine(result)).toEqual([{ label: "Neto", value: "$ 400.000,00" }]);
    expect(toCsv(result)).toBe("﻿Grupo;Neto\r\nADMIN — Administración;400000.0000\r\nid-2;");
  });
});
