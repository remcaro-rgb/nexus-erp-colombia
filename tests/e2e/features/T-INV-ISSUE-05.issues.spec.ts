// T-INV-ISSUE-05 (e2e, gate G3): UI: create/approve/post/print/reverse an issue per concept.
// World: tests/cross_module/test_xm_e2e_inventory_ops.py::world_inv_issue (gate world: real P09 inventory + issues, P05
// ledger + dimensions, P04 policy; P07 workflow and P17 renderer contract doubles). The five seeded concepts require
// approval, so each issue runs DRAFT → SUBMITTED → APPROVED (other user, D-023) → POSTED → REVERSED.
// IssuesPage / IssueConceptsPage are delivered by P09b but not registered in the inventory descriptor → augment.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

interface Case {
  code: string;
  name: string;
  role: string;
  reason?: string;
  dims: Array<"Centro de costo" | "Ítem contable (proyecto)">;
}

const CASES: Case[] = [
  { code: "CONSUMO", name: "Consumo interno a centro de costo", role: "INVENTORY_CONSUMPTION_EXPENSE", dims: ["Centro de costo"] },
  {
    code: "CONSUMO_DIRECTO",
    name: "Consumo directo a proyecto u obra",
    role: "INVENTORY_DIRECT_CONSUMPTION",
    dims: ["Centro de costo", "Ítem contable (proyecto)"],
  },
  {
    code: "BAJA",
    name: "Baja de inventario (deterioro, vencimiento, pérdida)",
    role: "INVENTORY_SCRAP_EXPENSE",
    reason: "VENCIDO lote de prueba (SINTÉTICO)",
    dims: [],
  },
  { code: "MUESTRA", name: "Muestras comerciales", role: "INVENTORY_SAMPLES_EXPENSE", dims: [] },
  { code: "OTRO", name: "Otras salidas", role: "INVENTORY_OTHER_ISSUE_EXPENSE", reason: "Donación a fundación (SINTÉTICA)", dims: [] },
];

test("T-INV-ISSUE-05 create, approve, post, print and reverse an issue per seeded concept (keyboard + labels)", async ({ app }) => {
  test.setTimeout(240_000);
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");

  // 0. Maestros › Conceptos de salida: the five seeded concepts with their rules (dimensions, reason, approval)
  await app.open("/m/inventario/maestros/conceptos-salida");
  await expect(page.getByRole("heading", { name: "Conceptos de salida" })).toBeVisible();
  const conceptsTable = page.getByRole("table", { name: "Conceptos" });
  for (const c of CASES) {
    const row = conceptsTable.getByRole("row").filter({ has: page.getByRole("cell", { name: c.code, exact: true }) });
    await expect(row.getByRole("cell").nth(1)).toHaveText(c.name);
    await expect(row.getByRole("cell").nth(2)).toHaveText(c.role);
    await expect(row.getByRole("cell").nth(3)).toHaveText(c.dims.length ? c.dims.join(", ") : "—");
    await expect(row.getByRole("cell").nth(5)).toHaveText("Siempre");
    await expect(row.getByRole("cell").nth(6)).toHaveText("Activo");
  }
  await expect(
    conceptsTable.getByRole("row").filter({ has: page.getByRole("cell", { name: "BAJA", exact: true }) }).getByRole("cell").nth(4),
  ).toHaveText("VENCIDO, DANADO, OBSOLETO, PERDIDA, OTRO");
  await expectLabelledControls(page);

  // 1. Movimientos › Salidas: one issue per concept
  await app.open("/m/inventario/movimientos/salidas");
  await expect(page.getByRole("heading", { name: "Salidas de inventario por concepto" })).toBeVisible();
  const issues = page.getByRole("table", { name: "Salidas" });
  const message = main.getByRole("status").filter({ hasText: /^Salida |: / });

  for (const [n, c] of CASES.entries()) {
    const expense = d.accounts[c.role] as string;

    // create (bodega): concept, warehouse, reason / dimensions the concept requires, one line of 2 UND
    await page.getByRole("combobox", { name: /^Concepto/ }).selectOption({ label: `${c.code} — ${c.name}` });
    await page.getByRole("combobox", { name: /^Bodega/ }).selectOption({ label: d.warehouse_label });
    if (c.reason) await page.getByRole("textbox", { name: /^Motivo/ }).fill(c.reason);
    else await expect(page.getByRole("textbox", { name: /^Motivo/ })).toHaveCount(0);
    for (const dim of ["Centro de costo", "Ítem contable (proyecto)"] as const) {
      const box = page.getByRole("textbox", { name: dim, exact: true });
      if (c.dims.includes(dim)) await box.fill(dim === "Centro de costo" ? d.cost_center_id : d.project_id);
      else await expect(box).toHaveCount(0);
    }
    await page.getByRole("textbox", { name: "Id del producto", exact: true }).fill(d.item_id);
    await page.getByRole("textbox", { name: "Cantidad", exact: true }).fill("2");
    await page.getByRole("button", { name: "Agregar línea", exact: true }).click();
    await expect(page.getByRole("list", { name: "Líneas de la salida" })).toContainText(`${d.item_id} × 2`);
    const createBtn = page.getByRole("button", { name: "Crear borrador", exact: true });
    if (n === 0) {
      // keyboard: the main action is reachable with Tab and fires with Enter
      await page.getByRole("textbox", { name: "Cantidad", exact: true }).focus();
      await tabTo(page, createBtn);
      await page.keyboard.press("Enter");
    } else {
      await createBtn.click();
    }
    await expect(message).toHaveText(/^Salida SAL-\S+ creada en borrador$/);
    const number = ((await message.textContent()) ?? "").split(" ")[1];
    const row = issues.getByRole("row").filter({ has: page.getByRole("cell", { name: number, exact: true }) });
    await expect(row.getByRole("cell").nth(2)).toHaveText(c.code);
    await expect(row.getByRole("cell").nth(3)).toHaveText("Borrador");
    if (n === 0) await expectLabelledControls(page);

    // simulate: journal preview Dr concept expense / Cr inventory, 2 × 1.000 at average cost
    await row.getByRole("button", { name: "Simular", exact: true }).click();
    const preview = page.getByRole("table", { name: "Vista previa contable" });
    const dr = preview.getByRole("row").filter({ hasText: expense });
    const cr = preview.getByRole("row").filter({ hasText: "143505" });
    await expect(dr.getByRole("cell").nth(1)).toHaveText("$ 2.000,00");
    await expect(dr.getByRole("cell").nth(2)).toHaveText("$ 0,00");
    await expect(cr.getByRole("cell").nth(1)).toHaveText("$ 0,00");
    await expect(cr.getByRole("cell").nth(2)).toHaveText("$ 2.000,00");
    await expect(preview.getByRole("row")).toHaveCount(3);

    // submit → pending approval (the concept requires approval)
    await row.getByRole("button", { name: "Enviar a aprobación", exact: true }).click();
    await expect(message).toHaveText(`Salida ${number}: Pendiente de aprobación`);
    await expect(row.getByRole("cell").nth(3)).toHaveText("Pendiente de aprobación");

    // approve as another user (the requester never approves, D-023)
    await app.as("supervisor");
    await expect(row.getByRole("cell").nth(3)).toHaveText("Pendiente de aprobación");
    await row.getByRole("button", { name: "Aprobar", exact: true }).click();
    await expect(message).toHaveText(`Salida ${number}: Aprobada`);
    await expect(row.getByRole("cell").nth(3)).toHaveText("Aprobada");

    // post (bodega): stock out + journal in one transaction; the value comes from the valuation (2 × 1.000)
    await app.as("bodega");
    const postBtn = row.getByRole("button", { name: "Contabilizar", exact: true });
    if (n === 0) {
      await row.getByRole("button", { name: "Simular", exact: true }).focus();
      await tabTo(page, postBtn);
      await page.keyboard.press("Enter");
    } else {
      await postBtn.click();
    }
    await expect(message).toHaveText(`Salida ${number}: Contabilizada`);
    await expect(row.getByRole("cell").nth(3)).toHaveText("Contabilizada");
    await expect(row.getByRole("cell").nth(4)).toHaveText("$ 2.000,00");

    // print: the UI must offer a print/PDF action on the posted issue
    const printBtn = row.getByRole("button", { name: /Imprimir|PDF/ }).or(row.getByRole("link", { name: /Imprimir|PDF/ }));
    if ((await printBtn.count()) > 0) {
      const [download] = await Promise.all([page.waitForEvent("download"), printBtn.first().click()]);
      expect(download.suggestedFilename()).toBe(`salida-${number}.pdf`);
    } else {
      await app.gap("DEF-P22-030", "Salidas: no Imprimir/PDF action on an issue (IssuesPage.tsx offers no call to GET /issues/{id}/pdf)", async () => {
        const list = await app.api("bodega").get("/api/v1/inventory/issues", { limit: "100" });
        const doc = list.items.find((x: any) => x.number === number);
        expect(doc, "issue listed by the API").toBeTruthy();
        const r = await page.request.get(`${app.world.api_origin}/api/v1/inventory/issues/${doc.id}/pdf`, {
          headers: { Authorization: `Bearer ${app.world.tokens.bodega}`, "X-Company-Id": d.company_id },
        });
        expect(r.status()).toBe(200);
        expect(r.headers()["content-type"]).toContain("application/pdf");
        expect(r.headers()["content-disposition"]).toContain(`salida-${number}.pdf`);
      });
    }

    // reverse with a reason (mirror entry + stock back at the original cost/bin)
    page.once("dialog", (dlg) => void dlg.accept(`Reverso de prueba ${c.code} (SINTÉTICO)`));
    await row.getByRole("button", { name: "Reversar", exact: true }).click();
    await expect(message).toHaveText(`Salida ${number}: Reversada`);
    await expect(row.getByRole("cell").nth(3)).toHaveText("Reversada");
    await expect(row.getByRole("button")).toHaveCount(0);
  }
  await expect(issues.getByRole("row").filter({ hasText: "Reversada" })).toHaveCount(CASES.length);
  await expectLabelledControls(page);

  // every reversal returned the stock: 50 UND on hand again, nothing reserved
  await app.open("/m/inventario/existencias/por-ubicacion");
  await page.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  const stock = page.getByRole("table", { name: "Existencias por ubicación y lote" });
  await expect(stock.getByRole("row")).toHaveCount(2);
  const stockRow = stock.getByRole("row").nth(1);
  await expect(stockRow.getByRole("cell").nth(3)).toHaveText("50");
  await expect(stockRow.getByRole("cell").nth(4)).toHaveText("0");
  await expect(stockRow.getByRole("cell").nth(5)).toHaveText("50");
});
