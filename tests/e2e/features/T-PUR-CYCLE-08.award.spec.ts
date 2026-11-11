// T-PUR-CYCLE-08 (e2e, gate G3): UI: comparison screen and award flow (Compras › Cotizaciones, F-PUR-CYCLE, D-009 /
// D-010 / D-023). World: tests/cross_module/test_xm_e2e_purchasing.py::world_pur_cycle (P11b kit: real P11 purchasing,
// P09 inventory, P04 policy, P05 ledger): RFQ to 3 vendors, 3 quotes, closed at its deadline.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

test("T-PUR-CYCLE-08 comparison matrix, recommendation, award with reason and SoD award approval (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");
  const vendor = (code: string) => `${code} — ${d.vendors[code]}`;
  const rfqRow = main.getByRole("table", { name: "Solicitudes de cotización" }).getByRole("row").filter({ hasText: d.rfq_number });

  // 1. Solicitudes de cotización (buyer comprador): the RFQ is closed at its deadline, 3 vendors invited
  await app.open("/m/compras/cotizaciones/rfq", "comprador");
  await expect(page.getByRole("tab", { name: "Solicitudes de cotización" })).toHaveAttribute("aria-selected", "true");
  await expect(rfqRow.getByRole("cell").nth(1)).toHaveText("Cerrada");
  await expect(rfqRow.getByRole("cell").nth(3)).toHaveText("3");
  await expect(rfqRow.getByRole("link", { name: "Ver comparativo" })).toHaveCount(0);
  const api = app.api("comprador");
  await app.gap(
    "DEF-P22-051",
    "Compras › Cotizaciones offers no action to evaluate a closed RFQ (RfqListPage renders no Evaluar/Cerrar control; PurchasingApi.rfqAction is never used), so the comparison snapshot cannot be created in the UI",
    async () => api.post(`/api/v1/purchasing/rfqs/${d.rfq_id}:evaluate`),
  );
  await page.reload();
  await expect(rfqRow.getByRole("cell").nth(1)).toHaveText("Evaluada");
  await expectLabelledControls(page);

  // 2. Cotizaciones recibidas: the three quotes, all on time
  await app.open(`/m/compras/cotizaciones/recibidas?rfq=${d.rfq_id}`);
  const quotes = main.getByRole("table", { name: "Cotizaciones recibidas" });
  await expect(quotes.getByRole("row")).toHaveCount(4);
  for (const [code, total] of [["PROV-A", "$ 300.000"], ["PROV-B", "$ 290.000"], ["PROV-C", "$ 350.000"]]) {
    const q = quotes.getByRole("row").filter({ hasText: d.vendors[code] });
    await expect(q.getByRole("cell").nth(3)).toHaveText("Recibida");
    await expect(q.getByRole("cell").nth(4)).toHaveText(total);
  }

  // 3. Comparación, opened from the RFQ list with the keyboard
  await app.open("/m/compras/cotizaciones/rfq");
  const link = rfqRow.getByRole("link", { name: "Ver comparativo" });
  await tabTo(page, link);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/m/compras/cotizaciones/comparacion\\?rfq=${d.rfq_id}$`));
  await expect(main.getByRole("heading", { name: `Comparativo ${d.rfq_number} · versión 1` })).toBeVisible();
  await expect(main.getByText("Estado: Evaluada")).toBeVisible();

  // the matrix agrees with the server's immutable comparison snapshot (normalized COP totals, D-009 points, score)
  const cmp = await api.get(`/api/v1/purchasing/rfqs/${d.rfq_id}/comparison`);
  expect(cmp.ranked.map((q: any) => q.vendor_code)).toHaveLength(3);
  const top = cmp.ranked[0];
  expect(cmp.recommended_quote_id).toBe(top.quote_id);
  expect(top.vendor_code).toBe("PROV-B"); // cheapest landed total and 3-day lead time
  const matrix = main.getByRole("table", { name: "Matriz de comparación" });
  await expect(matrix.getByRole("row")).toHaveCount(4);
  const totals: Record<string, string> = { "PROV-A": "$ 300.000", "PROV-B": "$ 290.000", "PROV-C": "$ 350.000" };
  for (const q of cmp.ranked) {
    const cells = matrix.getByTestId(`row-${q.quote_id}`).getByRole("cell");
    await expect(cells.nth(0)).toHaveText(String(q.rank));
    await expect(cells.nth(1)).toContainText(vendor(q.vendor_code));
    await expect(cells.nth(2)).toHaveText(totals[q.vendor_code]);
    await expect(cells.last()).toHaveText(q.score);
    await expect(cells.nth(1).getByText("Recomendada", { exact: true })).toHaveCount(q.quote_id === top.quote_id ? 1 : 0);
  }
  await expect(main.getByText(`Recomendada: PROV-B (${top.score} puntos)`, { exact: true })).toBeVisible();
  await main.getByText("Explicación de la recomendación").click();
  await expect(main.getByTestId("explanation")).toHaveText(cmp.explanation);
  expect(cmp.explanation.length).toBeGreaterThan(0);
  await expectLabelledControls(page);

  // 4. Award to PROV-A (not the recommendation): the reason is mandatory, then the award waits for approval
  const awardBtn = main.getByRole("button", { name: "Adjudicar", exact: true });
  await tabTo(page, awardBtn);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Adjudicar cotización" });
  await expect(dialog).toBeVisible();
  const quoteA = cmp.ranked.find((q: any) => q.vendor_code === "PROV-A");
  await expect(dialog.getByRole("radio", { name: `${vendor("PROV-B")} (${top.score} puntos)` })).toBeChecked();
  await dialog.getByRole("radio", { name: `${vendor("PROV-A")} (${quoteA.score} puntos)` }).check();
  const sendAward = dialog.getByRole("button", { name: "Enviar a aprobación", exact: true });
  await expect(dialog.getByText("La adjudicación difiere de la recomendación: indique el motivo.")).toBeVisible();
  await expect(sendAward).toBeDisabled();
  await dialog.getByLabel("Motivo (obligatorio: difiere de la recomendación)").fill("Mejor historial de calidad (SINTÉTICO)");
  await expect(sendAward).toBeEnabled();
  await expectLabelledControls(page, "[role=dialog]");
  await sendAward.focus();
  await page.keyboard.press("Enter");
  await expect(dialog).toBeHidden();
  await expect(main.getByText("Estado: Adjudicación por aprobar")).toBeVisible();
  await expect(main.getByRole("button", { name: "Adjudicar", exact: true })).toHaveCount(0);
  const pending = await api.get(`/api/v1/purchasing/rfqs/${d.rfq_id}`);
  expect([pending.award.status, pending.award.differs_from_recommendation, pending.award.reason]).toEqual([
    "PENDING_APPROVAL",
    true,
    "Mejor historial de calidad (SINTÉTICO)",
  ]);
  expect((await api.get("/api/v1/purchasing/orders")).items.filter((o: any) => o.rfq?.id === d.rfq_id)).toEqual([]);

  // SoD (D-023): the buyer who proposed the award cannot approve it; the server's refusal is shown
  // (the comparison page offers the approval to every holder of purchasing.rfq.approve, the server decides)
  const ownApprove = main.getByRole("button", { name: "Aprobar adjudicación", exact: true });
  await expect(ownApprove).toHaveCount(1);
  await ownApprove.click();
  await expect(main.getByRole("alert")).toContainText("sod_violation");
  await expect(main.getByText("Estado: Adjudicación por aprobar")).toBeVisible();

  // 5. The approver (aprobador, not the buyer) approves the award with the keyboard -> purchase order created
  await app.as("aprobador");
  await app.open(`/m/compras/cotizaciones/comparacion?rfq=${d.rfq_id}`);
  await expect(main.getByText("Estado: Adjudicación por aprobar")).toBeVisible();
  const approve = main.getByRole("button", { name: "Aprobar adjudicación", exact: true });
  await tabTo(page, approve);
  await page.keyboard.press("Enter");
  await expect(main.getByText("Estado: Orden creada")).toBeVisible();
  await expect(main.getByRole("button", { name: "Aprobar adjudicación", exact: true })).toHaveCount(0);
  await expectLabelledControls(page);

  // the DRAFT purchase order of PROV-A (100 × COP 3 000) originates from the RFQ (Compras › Órdenes)
  await app.as("comprador");
  await app.open("/m/compras/ordenes/lista");
  const po = main.getByRole("table", { name: "Órdenes de compra" }).getByRole("row").filter({ hasText: d.rfq_number });
  await expect(po).toHaveCount(1);
  await expect(po.getByRole("cell").nth(1)).toHaveText(d.vendors["PROV-A"]);
  await expect(po.getByRole("cell").nth(2)).toHaveText("Borrador");
  await expect(po.getByRole("cell").nth(4)).toHaveText("$ 300.000");
});
