import { describe, expect, it } from "vitest";
import { Dec } from "@lib/money";
import type { OpportunityDto, PipelineDto } from "../api";
import { board, loseErrors, weightedPipeline } from "../model";

const PIPE: PipelineDto = {
  id: "p1",
  code: "B2B",
  name: "Ventas B2B",
  status: "ACTIVE",
  stages: [
    { code: "PROSPECCION", name: "Prospección", probability: "10" },
    { code: "PROPUESTA", name: "Propuesta", probability: "50" },
  ],
};

function opp(id: string, stage: string, amount: string | null, status: OpportunityDto["opportunity_status"] = "OPEN"): OpportunityDto {
  const money = (v: string) => ({ amount: v, currency: "COP" });
  return {
    id,
    code: id,
    name: id,
    status: "ACTIVE",
    opportunity_status: status,
    party: null,
    amount: amount == null ? undefined : money(amount),
    weighted_amount: amount == null ? undefined : money(new Dec(amount).times(stage === "PROPUESTA" ? "0.5" : "0.1").toFixed()),
    stage,
    close_date: null,
    probability: stage === "PROPUESTA" ? "50" : "10",
    pipeline_id: "p1",
    owner_id: null,
  };
}

describe("crm view model", () => {
  it("groups OPEN opportunities by pipeline stage with decimal totals", () => {
    const cols = board(PIPE, [opp("a", "PROSPECCION", "1000.10"), opp("b", "PROPUESTA", "2000"), opp("c", "PROPUESTA", "500", "WON")]);
    expect(cols.map((c) => [c.stage, c.items.length, c.total])).toEqual([
      ["PROSPECCION", 1, "1000.1"],
      ["PROPUESTA", 1, "2000"],
    ]);
    expect(weightedPipeline([opp("a", "PROSPECCION", "1000"), opp("b", "PROPUESTA", "2000")])).toBe("1100");
  });

  it("masks totals when amounts are confidential for the user", () => {
    const cols = board(PIPE, [opp("a", "PROSPECCION", null)]);
    expect(cols[0]?.total).toBeNull();
    expect(weightedPipeline([opp("a", "PROSPECCION", null)])).toBeNull();
  });

  it("losing needs a reason", () => {
    expect(loseErrors(" ")).toHaveLength(1);
    expect(loseErrors("precio")).toEqual([]);
  });
});
