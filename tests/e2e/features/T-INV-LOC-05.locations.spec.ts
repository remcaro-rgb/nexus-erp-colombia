// T-INV-LOC-05 (e2e, gate G3): UI: create bins, receive, move, see stock per bin.
// World: tests/cross_module/test_xm_e2e_inventory_ops.py::world_inv_loc (gate world: real P09 inventory + locations,
// P11a purchasing receipts, P05 ledger, P04 policy). Warehouse CENTRAL has the default bin tree (D-014); a SENT purchase
// order of 10 UND PERNO-SYN waits to be received.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

test("T-INV-LOC-05 create bins, receive into a bin, move between bins, stock per bin (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");
  const wh = d.warehouse_code as string;
  const api = app.api("bodega");

  // 1. Maestros › Bodegas y ubicaciones: the default tree of the warehouse
  await app.open("/m/inventario/maestros/bodegas-ubicaciones");
  await expect(page.getByRole("heading", { name: "Bodegas y ubicaciones" })).toBeVisible();
  await page.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  const tree = page.getByRole("tree", { name: "Árbol de ubicaciones" });
  await expect(tree.getByRole("treeitem", { name: /^RECEPCION — Recepción · INTERNAL/ })).toBeVisible();
  await expectLabelledControls(page);

  // 2. create bins: shelf ESTANTE-A with the leaf bins A-01 and A-02
  const createControl = main.getByRole("button", { name: /Nueva ubicación|Crear ubicación|Agregar ubicación/ });
  expect(await createControl.count(), "a create-location control on Bodegas y ubicaciones").toBe(0);
  const bins = await app.gap(
    "DEF-P22-033",
    "Bodegas y ubicaciones: no form to create a location/bin (WarehousesPage.tsx only lists the tree; POST /warehouses/{id}/locations has no UI)",
    async () => {
      const base = `/api/v1/inventory/warehouses/${d.warehouse_id}/locations`;
      const shelf = await api.post(base, { code: "ESTANTE-A", name: "Estantería A (SINTÉTICA)", location_type: "INTERNAL", parent_id: d.root_location_id });
      const a1 = await api.post(base, { code: "A-01", name: "Estante A nivel 1", location_type: "INTERNAL", parent_id: shelf.id });
      const a2 = await api.post(base, { code: "A-02", name: "Estante A nivel 2", location_type: "INTERNAL", parent_id: shelf.id });
      return { shelf, a1, a2 };
    },
  );
  await app.open("/m/inventario/maestros/bodegas-ubicaciones");
  await page.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  const shelfItem = tree.getByRole("treeitem", { name: /^ESTANTE-A — Estantería A/ });
  await expect(shelfItem).toHaveAttribute("aria-expanded", "true");
  await expect(shelfItem.getByRole("group").getByRole("treeitem")).toHaveCount(2);
  await expect(shelfItem.getByRole("treeitem", { name: /^A-01 — Estante A nivel 1 · INTERNAL · 0$/ })).toHaveAttribute("aria-level", "3");
  await expect(shelfItem.getByRole("treeitem", { name: /^A-02 — Estante A nivel 2 · INTERNAL · 0$/ })).toBeVisible();
  // keyboard: switch to the table view with Tab + Enter
  const toggle = page.getByRole("button", { name: "Ver tabla", exact: true });
  await page.getByRole("combobox", { name: "Bodega", exact: true }).focus();
  await tabTo(page, toggle);
  await page.keyboard.press("Enter");
  const table = page.getByRole("table", { name: "Ubicaciones de la bodega" });
  const pathRow = (path: string) => table.getByRole("row").filter({ has: page.getByRole("cell", { name: path, exact: true }) });
  await expect(pathRow(`${wh}/ESTANTE-A`).getByRole("cell").nth(2)).toHaveText("No"); // parent: not postable
  await expect(pathRow(`${wh}/ESTANTE-A/A-01`).getByRole("cell").nth(2)).toHaveText("Sí");
  await expect(pathRow(`${wh}/ESTANTE-A/A-02`).getByRole("cell").nth(2)).toHaveText("Sí");
  await expectLabelledControls(page);

  // 3. receive the order into bin A-01
  await app.gap(
    "DEF-P22-034",
    "Compras › Recepciones: no UI to create a goods receipt from a sent order nor to choose the target bin (ReceiptsPage.tsx only lists/validates; POST /purchasing/receipts with lines[].location_id has no UI)",
    () =>
      api.post("/api/v1/purchasing/receipts", {
        order: { id: d.order_id },
        warehouse_id: d.warehouse_id,
        lines: [{ order_line_id: d.order_line_id, quantity: { value: "10", uom: "UND" }, location_id: bins.a1.id }],
      }),
  );
  await app.open("/m/compras/ordenes/recepciones");
  const receipts = page.getByRole("table", { name: "Recepciones" });
  const receipt = receipts.getByRole("row").filter({ has: page.getByRole("cell", { name: d.order_number, exact: true }) });
  await expect(receipt.getByRole("cell").nth(2)).toHaveText("Borrador");
  const validate = receipt.getByRole("button", { name: "Validar", exact: true });
  await tabTo(page, validate);
  await page.keyboard.press("Enter");
  await expect(main.getByRole("status")).toHaveText("Recepción validada: inventario y contabilidad actualizados.");
  await expect(receipt.getByRole("cell").nth(2)).toHaveText("Validada");
  await expect(receipt.getByRole("cell").nth(3)).toContainText("15.000");
  await expectLabelledControls(page);

  // 4. Movimientos › Entre ubicaciones: move 4 UND A-01 → A-02 (keyboard submit)
  await app.open("/m/inventario/movimientos/entre-ubicaciones");
  const form = page.getByRole("form", { name: "Mover entre ubicaciones" });
  await form.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  await form.getByRole("combobox", { name: "Producto", exact: true }).selectOption({ label: d.item_label });
  const from = form.getByRole("combobox", { name: "Ubicación origen", exact: true });
  await expect(from.getByRole("option", { name: `${wh}/ESTANTE-A/A-01`, exact: true })).toHaveCount(1);
  await expect(from.getByRole("option", { name: `${wh}/ESTANTE-A`, exact: true })).toHaveCount(0); // only leaf bins
  await from.selectOption({ label: `${wh}/ESTANTE-A/A-01` });
  await form.getByRole("combobox", { name: "Ubicación destino", exact: true }).selectOption({ label: `${wh}/ESTANTE-A/A-02` });
  await form.getByRole("textbox", { name: "Cantidad (unidad base)", exact: true }).fill("4");
  await tabTo(page, form.getByRole("button", { name: "Mover", exact: true }));
  await page.keyboard.press("Enter");
  await expect(main.getByRole("status")).toHaveText("Movimiento registrado");
  await expectLabelledControls(page);

  // 5. Existencias › Por ubicación: 6 in A-01, 4 in A-02, nothing left in RECEPCION
  await app.open("/m/inventario/existencias/por-ubicacion");
  await page.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  const stock = page.getByRole("table", { name: "Existencias por ubicación y lote" });
  const binRow = (code: string) => stock.getByRole("row").filter({ has: page.getByRole("cell", { name: code, exact: true }) });
  for (const [code, qty] of [["A-01", "6"], ["A-02", "4"]] as const) {
    await expect(binRow(code).getByRole("cell").nth(1)).toHaveText("INTERNAL");
    await expect(binRow(code).getByRole("cell").nth(3)).toHaveText(qty);
    await expect(binRow(code).getByRole("cell").nth(4)).toHaveText("0");
    await expect(binRow(code).getByRole("cell").nth(5)).toHaveText(qty);
  }
  for (const code of ["RECEPCION", "ALMACEN"]) {
    const rows = binRow(code);
    if ((await rows.count()) > 0) await expect(rows.getByRole("cell").nth(3)).toHaveText("0");
  }
  await expectLabelledControls(page);

  // and the bin tree shows the same figures per bin
  await app.open("/m/inventario/maestros/bodegas-ubicaciones");
  await page.getByRole("combobox", { name: "Bodega", exact: true }).selectOption({ label: d.warehouse_label });
  await expect(tree.getByRole("treeitem", { name: /^A-01 — Estante A nivel 1 · INTERNAL · 6$/ })).toBeVisible();
  await expect(tree.getByRole("treeitem", { name: /^A-02 — Estante A nivel 2 · INTERNAL · 4$/ })).toBeVisible();
});
