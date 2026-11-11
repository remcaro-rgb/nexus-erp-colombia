// T-INV-PRES-05 (e2e, gate G3): UI: create presentation, receive by presentation, see stock per presentation.
// World: tests/cross_module/e2e_worlds.py::inv_pres (P11b kit: real P09 inventory, P11a purchasing, P04 policy, P05 ledger).
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

test("T-INV-PRES-05 create presentation, receive by presentation, stock per presentation (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const itemOption = `${d.item_code} — ${d.item_name} (UND)`;

  // 1. Inventario › Maestros › Presentaciones: create "caja x12" for the item (keyboard-reachable form, labelled fields)
  await app.open("/m/inventario/maestros/presentaciones");
  await expect(page.getByRole("heading", { name: "Presentaciones" })).toBeVisible();
  const product = page.getByRole("combobox", { name: "Producto", exact: true });
  await product.selectOption({ label: itemOption });
  await product.focus();
  const form = page.getByRole("form", { name: "Nueva presentación" });
  await expect(form).toBeVisible();
  await tabTo(page, form.getByLabel("Código", { exact: true }));
  await page.keyboard.type("CJ12");
  await form.getByLabel("Nombre", { exact: true }).fill("Caja x12 (SINTÉTICO)");
  await form.getByLabel("Unidad de empaque", { exact: true }).fill("CAJA");
  await form.getByLabel("Factor a unidad base", { exact: true }).fill("12");
  await form.getByLabel("Código de barras", { exact: true }).fill("");
  await form.getByRole("button", { name: "Crear presentación" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main").getByRole("status")).toHaveText("Presentación creada");
  const row = page.getByRole("row").filter({ hasText: "CJ12" });
  await expect(row).toContainText("Caja x12 (SINTÉTICO)");
  await expect(row).toContainText("12");
  await expectLabelledControls(page);

  // 2. receive 3 boxes by presentation against the sent purchase order
  const api = app.api("bodega");
  const pres = (await api.get(`/api/v1/inventory/items/${d.item_id}/presentations`)).items.find((p: any) => p.code === "CJ12");
  expect(pres, "presentation created in the UI is returned by the API").toBeTruthy();
  await app.gap("DEF-P22-010", "no UI to receive goods by presentation (Compras › Recepciones has no presentation selector)", async () => {
    const receipt = await api.post("/api/v1/purchasing/receipts", {
      order: { id: d.order_id },
      warehouse_id: d.warehouse_id,
      lines: [{ order_line_id: d.order_line_id, quantity: { value: "36", uom: "UND" }, presentation_id: pres.id }],
    });
    await api.post(`/api/v1/purchasing/receipts/${receipt.id}:validate`);
  });

  // 3. Existencias › Por presentación: 3 packages of 12 = 36 base units, nothing loose, canonical 36 (D-013: never double counted)
  await app.open("/m/inventario/existencias/por-presentacion");
  await expect(page.getByRole("heading", { name: "Existencias por presentación" })).toBeVisible();
  await page.getByRole("combobox", { name: "Producto", exact: true }).selectOption({ label: `${d.item_code} — ${d.item_name}` });
  const stock = page.getByRole("table", { name: "Stock empacado por presentación y suelto" });
  const presRow = stock.getByRole("row").filter({ hasText: "CJ12" });
  await expect(presRow.getByRole("cell").nth(1)).toHaveText("12");
  await expect(presRow.getByRole("cell").nth(2)).toHaveText("3");
  await expect(presRow.getByRole("cell").nth(4)).toHaveText("36");
  await expect(stock.getByRole("row").filter({ hasText: "Existencia canónica" })).toContainText("36");
  await expectLabelledControls(page);
});
