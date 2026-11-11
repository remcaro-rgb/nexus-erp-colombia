// T-TRE-RC-08 (e2e, gate G3): UI: create, approve, post, print, reverse an RC; keyboard and screen-reader labels.
// World: tests/cross_module/test_xm_e2e_treasury.py::world_rc (G2 gate world: real P04/P05/P16; RC approval threshold
// COP 100,000; concept PRESTAMO-UI → role CAPITAL; creator `tesorero`, approver `aprobador_tes`). Synthetic amounts.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";
import { apiPdf, createDraft, keyAction, printFromUi, row, statusOf } from "./support/treasury";

const LIST = "/m/tesoreria/recibos/lista";
const TABLE = "Recibos de caja";

test("T-TRE-RC-08 RC create, approve (SoD), post, print, reverse in the UI (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");

  // 1. Tesorería › Recibos de caja › Recibos: the entry form (labelled, keyboard) creates a DRAFT concept RC of 250,000
  await app.open(LIST);
  await expect(main.getByRole("tab", { name: "Recibos", exact: true })).toHaveAttribute("aria-selected", "true");
  await expectLabelledControls(page);
  await createDraft(page, "Nuevo recibos de caja", {
    fundLabel: /^BCO-1 · /,
    method: "Transferencia",
    lines: [{ type: "Concepto de tesorería", ref: d.concept_id, amount: "250000" }],
  });
  const draft = row(page, TABLE, "Concepto de tesorería");
  await expect(draft).toHaveCount(1);
  await expect(statusOf(draft)).toHaveText("Borrador");
  await expect(draft.getByRole("cell").nth(0)).toHaveText("—"); // never numbered before POST
  await expect(draft.getByRole("cell").nth(4)).toHaveText("$ 250.000");
  await expect(main.getByRole("alert")).toHaveCount(0);

  // 2. journal preview (:simulate): Dr fund 250,000 / Cr concept account 250,000, balanced
  await keyAction(page, draft, "Vista previa");
  const preview = page.getByRole("table", { name: "Vista previa del asiento" });
  await expect(preview).toBeVisible();
  const lines = preview.getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(lines).toHaveCount(2);
  await expect(lines.filter({ hasText: "CAPITAL" }).getByRole("cell").nth(2)).toHaveText("$ 250.000");
  await expect(lines.filter({ hasText: "CAPITAL" }).getByRole("cell").nth(1)).toHaveText("$ 0");
  await expect(lines.filter({ hasNotText: "CAPITAL" }).getByRole("cell").nth(1)).toHaveText("$ 250.000");

  // 3. the amount reaches the threshold: posting the DRAFT directly is refused; submit → "Por aprobar"
  await keyAction(page, draft, "Contabilizar");
  await expect(main.getByRole("alert")).toBeVisible();
  await expect(statusOf(draft)).toHaveText("Borrador");
  await keyAction(page, draft, "Enviar a aprobación");
  await expect(statusOf(draft)).toHaveText("Por aprobar");
  await expect(main.getByRole("alert")).toHaveCount(0);

  // 4. SoD (D-023): the creator cannot approve (the server refuses, the UI shows the error and the status is unchanged)
  await keyAction(page, draft, "Aprobar");
  await expect(main.getByRole("alert")).toBeVisible();
  await expect(statusOf(draft)).toHaveText("Por aprobar");

  // 5. another user (treasury approver only) approves; the approver gets no entry form and no post action
  await app.as("aprobador_tes");
  await expect(page.getByRole("table", { name: TABLE, exact: true })).toBeVisible();
  await expect(page.getByRole("form", { name: "Nuevo recibos de caja" })).toHaveCount(0);
  const pending = row(page, TABLE, "Por aprobar");
  await expect(pending).toHaveCount(1);
  await expectLabelledControls(page);
  const approve = pending.getByRole("button", { name: "Aprobar", exact: true });
  await tabTo(page, approve);
  await page.keyboard.press("Enter");
  await expect(statusOf(row(page, TABLE, "Aprobado"))).toHaveText("Aprobado");
  await expect(row(page, TABLE, "Aprobado").getByRole("button", { name: "Contabilizar" })).toHaveCount(0);

  // 6. the cashier posts: number RC-000001 assigned at POST, list caption totals the posted receipts
  await app.as("tesorero");
  const approved = row(page, TABLE, "Aprobado");
  await expect(approved).toHaveCount(1);
  await keyAction(page, approved, "Contabilizar");
  const posted = row(page, TABLE, "RC-000001");
  await expect(statusOf(posted)).toHaveText("Contabilizado");
  await expect(posted.getByRole("cell").nth(4)).toHaveText("$ 250.000");
  await expect(page.getByRole("table", { name: TABLE, exact: true }).locator("caption")).toHaveText("Total contabilizado: $ 250.000");
  const rc = (await app.api("tesorero").get("/api/v1/treasury/receipts", { number: "RC-000001" })).items.find((v: any) => v.number === "RC-000001");
  expect(rc?.status).toBe("POSTED");

  // 7. print: the UI must offer a print/PDF action yielding a PDF
  if (!(await printFromUi(page, posted))) {
    await app.gap("DEF-P22-020", "Tesorería › Recibos offers no print/PDF action for a posted RC (VouchersPage.tsx has no Imprimir/PDF control)", async () => {
      const pdf = await apiPdf(app, "receipts", rc.id, "tesorero");
      if (!pdf.ok)
        await app.gap("DEF-P22-021", `GET /api/v1/treasury/receipts/{id}/pdf (contract treasury_receipts_by_pdf_pdf) not served: HTTP ${pdf.status} ${pdf.type}`, async () => undefined);
    });
  }

  // 8. reverse with a reason (dialog field "Motivo"): original REVERSED, numbered mirror RC-000002 POSTED, caption nets
  const reverse = posted.getByRole("button", { name: "Reversar", exact: true });
  await reverse.focus();
  await page.keyboard.press("Enter");
  await expect(main.getByRole("alert")).toHaveText("Escriba el motivo antes de rechazar o reversar.");
  await page.getByLabel("Motivo (rechazo / reversión)", { exact: true }).fill("Consignación duplicada (SINTÉTICO)");
  await reverse.focus();
  await page.keyboard.press("Enter");
  await expect(statusOf(row(page, TABLE, "RC-000001"))).toHaveText("Reversado");
  const mirror = row(page, TABLE, "RC-000002");
  await expect(statusOf(mirror)).toHaveText("Contabilizado");
  await expect(main.getByRole("alert")).toHaveCount(0);
  const after = await app.api("tesorero").get(`/api/v1/treasury/receipts/${rc.id}`);
  expect(after.status).toBe("REVERSED");
  await expectLabelledControls(page);
});
