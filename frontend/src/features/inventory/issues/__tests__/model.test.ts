import { describe, expect, it } from "vitest";
import type { IssueConceptDto } from "../api";
import { formatCop, issueActions, requiredDimensions, validateDraft } from "../model";

function concept(over: Partial<IssueConceptDto>): IssueConceptDto {
  return {
    id: "c", code: "CONSUMO", name: "Consumo interno", status: "ACTIVE", kind: "CONSUMPTION", account_role: "INVENTORY_CONSUMPTION_EXPENSE",
    requires_dimension: true, required_dims: [], requires_reason: false, reason_codes: [], requires_approval: false, version: 1, ...over,
  };
}

describe("issue model", () => {
  it("T-INV-ISSUE-01 pre-validates required dimensions like the server", () => {
    const c = concept({ code: "CONSUMO_DIRECTO", required_dims: ["cost_center", "item_contable"] });
    expect(requiredDimensions(c)).toEqual(["cost_center", "item_contable"]);
    const missing = validateDraft(c, { concept_id: "c", warehouse_id: "w", lines: [{ item_id: "i", quantity: "2" }] });
    expect(missing.map((p) => p.code)).toEqual(["dimension_required", "dimension_required"]);
    const ok = validateDraft(c, { concept_id: "c", warehouse_id: "w", dimensions: { cost_center_id: "cc", item_dimension_id: "p" }, lines: [{ item_id: "i", quantity: "2" }] });
    expect(ok).toEqual([]);
  });

  it("T-INV-ISSUE-02 requires a coded reason for scraps", () => {
    const baja = concept({ code: "BAJA", requires_dimension: false, requires_reason: true, reason_codes: ["VENCIDO", "DANADO"] });
    const base = { concept_id: "c", warehouse_id: "w", lines: [{ item_id: "i", quantity: "1" }] };
    expect(validateDraft(baja, base).map((p) => p.code)).toEqual(["issue_reason_required"]);
    expect(validateDraft(baja, { ...base, reason: "ROTO" }).map((p) => p.code)).toEqual(["issue_reason_required"]);
    expect(validateDraft(baja, { ...base, reason: "VENCIDO: lote L-1" })).toEqual([]);
    expect(validateDraft(baja, { ...base, lines: [{ item_id: "i", quantity: "0" }], reason: "DANADO" }).map((p) => p.code)).toEqual(["quantity_invalid"]);
  });

  it("offers actions per state and formats COP without floats", () => {
    expect(issueActions("DRAFT")).toEqual(["simulate", "submit", "post"]);
    expect(issueActions("SUBMITTED")).toEqual(["approve"]);
    expect(issueActions("POSTED")).toEqual(["reverse"]);
    expect(issueActions("REVERSED")).toEqual([]);
    expect(formatCop("1234567.5050")).toBe("$ 1.234.567,51");
    expect(formatCop("0.004")).toBe("$ 0,00");
    expect(formatCop(null)).toBe("—");
  });
});
