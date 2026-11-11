// T-TRE-CE-07 (e2e, gate G3): UI: CE create/approve/post/print/reverse; batch execution flow.
// World: tests/cross_module/test_xm_e2e_treasury.py::world_ce (G2 gate world: real P04/P05/P11/P16; three posted vendor
// invoices of 119,000 = AP open items; bank funded by a capital RC; CE approval threshold COP 100,000; creator
// `tesorero`, approver `aprobador_tes`). Synthetic amounts.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";
import { apiPdf, createDraft, keyAction, printFromUi, row, statusOf } from "./support/treasury";

const LIST = "/m/tesoreria/egresos/lista";
const TABLE = "Comprobantes de egreso";
const FORM = "Nuevo comprobantes de egreso";

test("T-TRE-CE-07 CE create, approve (SoD), post, print, reverse and payment-batch execution in the UI (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");
  const [vendor, payee1, payee2] = d.vendors as Array<{ party_id: string; code: string }>;
  const [bill, bill1, bill2] = d.open_items as string[];

  // ---- A. single CE paying the vendor invoice (AP application 119,000) ------------------------------------------
  await app.open(LIST);
  await expect(main.getByRole("tab", { name: "Comprobantes", exact: true })).toHaveAttribute("aria-selected", "true");
  await expectLabelledControls(page);
  await createDraft(page, FORM, {
    party: vendor.party_id,
    fundLabel: /^BCO-1 · /,
    method: "Transferencia",
    lines: [{ type: "Aplicación a factura", ref: bill, amount: "119000" }],
  });
  const draft = row(page, TABLE, "Aplicación a factura");
  await expect(draft).toHaveCount(1);
  await expect(statusOf(draft)).toHaveText("Borrador");
  await expect(draft.getByRole("cell").nth(0)).toHaveText("—");
  await expect(draft.getByRole("cell").nth(4)).toHaveText("$ 119.000");
  await expect(main.getByRole("alert")).toHaveCount(0);

  // journal preview: Dr AP 119,000 / Cr fund 119,000
  await keyAction(page, draft, "Vista previa");
  const preview = page.getByRole("table", { name: "Vista previa del asiento" });
  const plines = preview.getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(plines).toHaveCount(2);
  await expect(plines.filter({ hasText: /^AP/ }).getByRole("cell").nth(1)).toHaveText("$ 119.000");
  await expect(plines.filter({ hasNotText: /^AP/ }).getByRole("cell").nth(2)).toHaveText("$ 119.000");

  // over the threshold: submit → "Por aprobar"; the creator cannot approve (SoD)
  await keyAction(page, draft, "Enviar a aprobación");
  await expect(statusOf(draft)).toHaveText("Por aprobar");
  await keyAction(page, draft, "Aprobar");
  await expect(main.getByRole("alert")).toBeVisible();
  await expect(statusOf(draft)).toHaveText("Por aprobar");

  // approver (other user) approves with the keyboard
  await app.as("aprobador_tes");
  await expect(page.getByRole("form", { name: FORM })).toHaveCount(0);
  const pending = row(page, TABLE, "Por aprobar");
  await expect(pending).toHaveCount(1);
  await tabTo(page, pending.getByRole("button", { name: "Aprobar", exact: true }));
  await page.keyboard.press("Enter");
  await expect(statusOf(row(page, TABLE, "Aprobado"))).toHaveText("Aprobado");
  await expectLabelledControls(page);

  // cashier posts → CE-000001
  await app.as("tesorero");
  await keyAction(page, row(page, TABLE, "Aprobado"), "Contabilizar");
  const posted = row(page, TABLE, "CE-000001");
  await expect(statusOf(posted)).toHaveText("Contabilizado");
  await expect(posted.getByRole("cell").nth(4)).toHaveText("$ 119.000");
  const api = app.api("tesorero");
  const ce = (await api.get("/api/v1/treasury/disbursements")).items.find((v: any) => v.number === "CE-000001");
  expect(ce?.status).toBe("POSTED");

  // print
  if (!(await printFromUi(page, posted))) {
    await app.gap("DEF-P22-022", "Tesorería › Egresos › Comprobantes offers no print/PDF action for a posted CE (VouchersPage.tsx has no Imprimir/PDF control)", async () => {
      const pdf = await apiPdf(app, "disbursements", ce.id, "tesorero");
      if (!pdf.ok)
        await app.gap("DEF-P22-023", `GET /api/v1/treasury/disbursements/{id}/pdf (contract) not served: HTTP ${pdf.status} ${pdf.type}`, async () => undefined);
    });
  }

  // reverse with a reason → CE-000001 REVERSED, mirror CE-000002, AP open item re-opened
  await page.getByLabel("Motivo (rechazo / reversión)", { exact: true }).fill("Pago a cuenta equivocada (SINTÉTICO)");
  await keyAction(page, posted, "Reversar");
  await expect(statusOf(row(page, TABLE, "CE-000001"))).toHaveText("Reversado");
  await expect(statusOf(row(page, TABLE, "CE-000002"))).toHaveText("Contabilizado");
  await expect(main.getByRole("alert")).toHaveCount(0);
  expect((await api.get(`/api/v1/treasury/disbursements/${ce.id}`)).status).toBe("REVERSED");

  // ---- B. payment batch: two payees → submit, approve (other user), execute → one numbered CE per payee -------------
  const lotesTab = main.getByRole("tab", { name: "Lotes de pago", exact: true });
  await lotesTab.click();
  await expect(page).toHaveURL(/\/m\/tesoreria\/egresos\/lotes$/);
  const newBatch = main.getByRole("form", { name: /Nuevo lote/i }).or(main.getByRole("button", { name: /Nuevo lote|Crear lote/i }));
  if ((await newBatch.count()) === 0) {
    await app.gap("DEF-P22-024", "Tesorería › Egresos › Lotes de pago has no form/action to create a payment batch (BatchesPage.tsx is list + actions only)", async () =>
      api.post("/api/v1/treasury/payment-batches", {
        document_date: d.today,
        payment_date: d.today,
        fund_id: d.fund_id,
        currency: "COP",
        lines: [
          { line_no: 1, party: { id: payee1.party_id }, open_item_id: bill1, applied_amount: { amount: "119000", currency: "COP" } },
          { line_no: 2, party: { id: payee2.party_id }, open_item_id: bill2, applied_amount: { amount: "119000", currency: "COP" } },
        ],
      }),
    );
    await page.reload();
  }
  const batches = page.getByRole("table", { name: "Lotes de pago", exact: true });
  const batch = batches.getByRole("row").filter({ hasText: "LP-" });
  await expect(batch).toHaveCount(1);
  await expect(statusOf(batch)).toHaveText("Borrador");
  await expect(batch.getByRole("cell").nth(3)).toHaveText("$ 238.000");
  await expectLabelledControls(page);
  await keyAction(page, batch, "Enviar a aprobación");
  await expect(statusOf(batch)).toHaveText("Por aprobar");
  await expect(main.getByRole("alert")).toHaveCount(0);

  await app.as("aprobador_tes");
  const b2 = page.getByRole("table", { name: "Lotes de pago", exact: true }).getByRole("row").filter({ hasText: "LP-" });
  await tabTo(page, b2.getByRole("button", { name: "Aprobar", exact: true }));
  await page.keyboard.press("Enter");
  await expect(statusOf(b2)).toHaveText("Aprobado");
  await expect(b2.getByRole("button", { name: "Ejecutar pagos" })).toHaveCount(0);

  await app.as("tesorero");
  const b3 = page.getByRole("table", { name: "Lotes de pago", exact: true }).getByRole("row").filter({ hasText: "LP-" });
  await keyAction(page, b3, "Ejecutar pagos");
  await expect(statusOf(b3)).toHaveText("Ejecutado");
  const file = main.getByRole("figure");
  await expect(file).toContainText("FIXTURE_NOT_CERTIFIED");
  await expect(file).toContainText("simulado, no certificado");
  await expect(file.locator("pre")).toContainText("lote,linea,comprobante,beneficiario,valor,referencia");
  await expect(file.locator("pre")).toContainText(",CE-000003,");
  await expect(file.locator("pre")).toContainText(",CE-000004,");

  // the executed batch produced numbered, posted CEs (gap-free after CE-000002) listed under Comprobantes
  await main.getByRole("tab", { name: "Comprobantes", exact: true }).click();
  await expect(page).toHaveURL(/\/m\/tesoreria\/egresos\/lista$/);
  for (const n of ["CE-000003", "CE-000004"]) {
    const r = row(page, TABLE, n);
    await expect(statusOf(r)).toHaveText("Contabilizado");
    await expect(r.getByRole("cell").nth(4)).toHaveText("$ 119.000");
  }
  await expect(row(page, TABLE, "CE-000005")).toHaveCount(0);
  await expectLabelledControls(page);
});
