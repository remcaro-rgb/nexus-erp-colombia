import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderDs } from "@app/test/render";
import type { PurchasingApi, RfqDto } from "../api";
import { ComparisonView } from "../rfq/ComparisonPage";
import { COMPARISON } from "./fixtures";

const RFQ: RfqDto = {
  id: "r1", number: "SC-000001", document_date: "2026-11-09", deadline: "2026-11-11T15:00:00Z", status: "EVALUATED",
  currency: "COP", invited_vendors: ["vA", "vB", "vC"], awarded_quote_id: null, award_reason: null, award: null, lines: [], version: 3,
};

function fakeApi() {
  return {
    rfq: vi.fn().mockResolvedValue(RFQ),
    comparison: vi.fn().mockResolvedValue(COMPARISON),
    award: vi.fn().mockResolvedValue({ ...RFQ, status: "AWARD_PENDING_APPROVAL" }),
    approveAward: vi.fn(),
  };
}

describe("ComparisonView", () => {
  it("T-PUR-CYCLE-04 award dialog requires a reason when the choice differs from the recommendation", async () => {
    const api = fakeApi();
    const user = userEvent.setup();
    renderDs(<ComparisonView api={api as unknown as PurchasingApi} rfqId="r1" canAward canApprove={false} />);
    expect(await screen.findByText("Recomendada: PROV-B (96.0000 puntos)")).toBeInTheDocument();
    expect(screen.getByText("Tardía: no puntúa hasta reabrir (D-010)")).toBeInTheDocument();
    expect(screen.getByTestId("explanation")).toHaveTextContent("Recomendación: PROV-B");

    await user.click(screen.getByRole("button", { name: "Adjudicar" }));
    await user.click(screen.getByLabelText(/PROV-A/));
    const send = screen.getByRole("button", { name: "Enviar a aprobación" });
    expect(send).toBeDisabled();
    expect(screen.getByText("La adjudicación difiere de la recomendación: indique el motivo.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox"), "Mejor historial de calidad");
    expect(send).toBeEnabled();
    await user.click(send);
    await waitFor(() => expect(api.award).toHaveBeenCalledWith("r1", "qA", "Mejor historial de calidad"));
  });

  it("hides the award and approval actions without the permission", async () => {
    renderDs(<ComparisonView api={fakeApi() as unknown as PurchasingApi} rfqId="r1" canAward={false} canApprove={false} />);
    await screen.findByText("Recomendada: PROV-B (96.0000 puntos)");
    expect(screen.queryByRole("button", { name: "Adjudicar" })).toBeNull();
  });
});
