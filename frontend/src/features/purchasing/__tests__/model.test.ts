import { describe, expect, it } from "vitest";
import type { OrderDto } from "../api";
import { awardErrors, differsFromRecommendation, matrixRows, recommendationBadge } from "../model";
import { progress } from "../orders/model";
import { COMPARISON } from "./fixtures";

describe("purchasing view model", () => {
  it("T-PUR-CYCLE-03 matrix keeps the server ranking and lists excluded quotes last with the D-010 reason", () => {
    const rows = matrixRows(COMPARISON);
    expect(rows.map((r) => [r.rank, r.recommended, r.excluded])).toEqual([
      [1, true, null],
      [2, false, null],
      [null, false, "Tardía: no puntúa hasta reabrir (D-010)"],
    ]);
    expect(recommendationBadge(COMPARISON)).toBe("Recomendada: PROV-B (96.0000 puntos)");
  });

  it("T-PUR-CYCLE-04 award differing from the recommendation needs a reason; excluded quotes cannot be awarded", () => {
    expect(awardErrors(COMPARISON, { quoteId: "qB", reason: "" })).toEqual([]);
    expect(differsFromRecommendation(COMPARISON, "qA")).toBe(true);
    expect(awardErrors(COMPARISON, { quoteId: "qA", reason: "  " })).toEqual(["La adjudicación difiere de la recomendación: indique el motivo."]);
    expect(awardErrors(COMPARISON, { quoteId: "qA", reason: "Calidad" })).toEqual([]);
    expect(awardErrors(COMPARISON, { quoteId: "qC", reason: "x" })).toEqual(["La cotización está excluida del comparativo."]);
    expect(awardErrors(COMPARISON, { quoteId: null, reason: "" })).toHaveLength(1);
  });

  it("order progress uses decimal arithmetic over all lines", () => {
    const order = {
      lines: [
        { id: "l1", line_no: 1, description: "", quantity: { value: "100", uom: "KG" }, unit_price: { amount: "1", currency: "COP" }, received_qty: "60", invoiced_qty: "0" },
        { id: "l2", line_no: 2, description: "", quantity: { value: "50", uom: "KG" }, unit_price: { amount: "1", currency: "COP" }, received_qty: "30", invoiced_qty: "30" },
      ],
    } as unknown as OrderDto;
    expect(progress(order)).toBe("60 % recibido · 20 % facturado");
  });
});
