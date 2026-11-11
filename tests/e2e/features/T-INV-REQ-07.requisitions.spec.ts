// T-INV-REQ-07 (e2e, gate G3): UI flow across the four tabs of Compras › Solicitudes internas
// (Mis solicitudes · Por aprobar · Abastecimiento · Consolidación), F-INV-REQ, D-011 / D-012 / D-023 / D-031.
// World: tests/cross_module/test_xm_e2e_purchasing.py::world_inv_req (P11b kit: real P09 inventory, P11 purchasing,
// P04 policy, P05 ledger). The tabs are delivered in features/purchasing/requisitions but not registered in the compras
// descriptor (CCR P11b-5) -> the runner uses `augment: true`.
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

test("T-INV-REQ-07 request, approval (SoD), sourcing split, consolidation into a draft PO across the four tabs (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");
  const requisitions = main.getByRole("table", { name: "Solicitudes internas" });

  // 1. Mis solicitudes (requester sol): request 10 UND for 2026-11-16 and send it to approval (keyboard)
  await app.open("/m/compras/solicitudes/mis-solicitudes", "sol");
  await expect(page.getByRole("tab", { name: "Mis solicitudes" })).toHaveAttribute("aria-selected", "true");
  const form = page.getByRole("form", { name: "Nueva solicitud interna" });
  await expect(form).toBeVisible();
  await form.getByLabel("Bodega que solicita (id)", { exact: true }).fill(d.tienda_id);
  await form.getByLabel("Se necesita el", { exact: true }).fill("2026-11-16");
  await form.getByLabel("Producto (id)", { exact: true }).fill(d.item_id);
  await form.getByLabel("Cantidad (unidad base)", { exact: true }).fill("10");
  const send = form.getByRole("button", { name: "Enviar a aprobación", exact: true });
  await tabTo(page, send);
  await page.keyboard.press("Enter");
  const sent = form.getByRole("status");
  await expect(sent).toHaveText(/^Se envió la solicitud SOL-\S+ a aprobación\.$/);
  const number = /SOL-\S+(?= a aprobación)/.exec((await sent.textContent()) ?? "")?.[0] ?? "";
  expect(number).toMatch(/^SOL-/);
  const mine = requisitions.getByRole("row").filter({ hasText: number });
  await expect(mine).toContainText("Enviada a aprobación");
  await expect(mine).toContainText("2026-11-16");
  // requesters see only their own requisitions (sol2's requisitions are not listed)
  await expect(requisitions.getByRole("row").filter({ hasText: d.sol2_number })).toHaveCount(0);
  await expectLabelledControls(page);
  // D-023 in the UI: the requester has no approval inbox tab
  await expect(page.getByRole("tab", { name: "Mis solicitudes" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Por aprobar" })).toHaveCount(0);

  // 2. Por aprobar (approver jefe, D-023: the requester never approves)
  await app.as("jefe");
  await app.open("/m/compras/solicitudes/por-aprobar");
  await expect(page.getByRole("tab", { name: "Por aprobar" })).toHaveAttribute("aria-selected", "true");
  const inboxRow = requisitions.getByRole("row").filter({ hasText: number });
  await expect(inboxRow).toContainText("Enviada a aprobación");
  await expect(requisitions.getByRole("row").filter({ hasText: d.pending_number })).toHaveCount(1);
  await expectLabelledControls(page);
  const approve = inboxRow.getByRole("button", { name: "Aprobar", exact: true });
  await tabTo(page, approve);
  await page.keyboard.press("Enter");
  await expect(main.getByRole("status")).toHaveText(`Solicitud ${number} aprobada y abastecida.`);
  await expect(requisitions.getByRole("row").filter({ hasText: number })).toHaveCount(0);
  await expect(requisitions.getByRole("row").filter({ hasText: d.pending_number })).toHaveCount(1);

  // the requester sees the result: in progress, 4 by transfer (CENTRAL, D-012) / 6 to purchase / 0 fulfilled
  await app.as("sol");
  await app.open("/m/compras/solicitudes/mis-solicitudes");
  await expect(mine).toContainText("En abastecimiento");
  await expect(mine.getByRole("cell").nth(4)).toHaveText("4 / 6 / 0");

  // 3. Abastecimiento (buyer comprador): the approved requisition with its transfer vs purchase split
  await app.as("comprador");
  await app.open("/m/compras/solicitudes/abastecimiento");
  await expect(page.getByRole("tab", { name: "Abastecimiento" })).toHaveAttribute("aria-selected", "true");
  await expect(main.getByText(/^Estados de línea:/)).toBeVisible();
  // product defect: the tab filters ?status=APPROVED, the approved requisition (IN_PROGRESS) is not offered
  await expect(main.getByText("No hay solicitudes aprobadas pendientes de abastecer")).toBeVisible();
  await expectLabelledControls(page);
  const api = app.api("comprador");
  const reqs = (await api.get("/api/v1/purchasing/internal-requisitions", { number })).items;
  expect(reqs.length).toBe(1);
  const req = reqs[0];
  await app.gap(
    "DEF-P22-050",
    "Abastecimiento lists only requisition status APPROVED (?status=APPROVED), but approval sources at once and moves the requisition to IN_PROGRESS: the approved requisition and its split (Simular / Aplicar) never appear",
    async () => {
      const sim = await api.post(`/api/v1/purchasing/internal-requisitions/${req.id}:source?mode=simulate`);
      expect(sim.data.lines).toEqual([]); // nothing left to source: the approval already applied D-012
    },
  );
  const line = req.lines[0];
  expect([line.line_status, line.qty_transfer, line.qty_procure]).toEqual(["TO_PROCURE", "4.000000", "6.000000"]);
  const transfer = line.lineage.find((e: any) => e.kind === "transfer");
  expect(transfer.from_warehouse_id).toBe(d.central_id);

  // the DRAFT transfer CENTRAL -> TIENDA is visible to the warehouse in Inventario › Movimientos › Traslados
  await app.as("bodega");
  await app.open("/m/inventario/movimientos/traslados");
  const trf = main.getByRole("table", { name: "Traslados" }).getByRole("row").filter({ hasText: transfer.number });
  await expect(trf).toHaveCount(1);
  await expect(trf.getByRole("cell").nth(2)).toHaveText(/DRAFT|SUBMITTED/);

  // 4. Consolidación (comprador): preview with lineage (D-031), then the run creates the D-011 draft purchase order
  await app.as("comprador");
  await app.open("/m/compras/solicitudes/consolidacion");
  await expect(page.getByRole("tab", { name: "Consolidación" })).toHaveAttribute("aria-selected", "true");
  const preview = main.getByRole("button", { name: "Vista previa", exact: true });
  await tabTo(page, preview);
  await page.keyboard.press("Enter");
  const previewTable = main.getByRole("table", { name: "Vista previa de consolidación" });
  await expect(previewTable.getByRole("row")).toHaveCount(2);
  const group = previewTable.getByRole("row").nth(1).getByRole("cell");
  await expect(group.nth(1)).toHaveText("2026-11-16");
  await expect(group.nth(2)).toHaveText("18.000000");
  await expect(group.nth(3)).toHaveText(`${number}·1·6.000000, ${d.sol2_number}·1·12.000000`);
  await expect(group.nth(4)).toHaveText("Borrador de orden de compra (requiere aprobación)");
  await expectLabelledControls(page);
  await main.getByRole("button", { name: "Consolidar y crear borradores", exact: true }).click();
  const applied = main.getByRole("table", { name: "Consolidación aplicada" });
  await expect(applied.getByRole("row")).toHaveCount(2);
  await expect(applied.getByRole("row").nth(1).getByRole("cell").nth(2)).toHaveText("18.000000");

  // the draft PO (18 × COP 2 500, still needs approval) is in Compras › Órdenes; the requisition line is IN_PURCHASE
  await app.open("/m/compras/ordenes/lista");
  const orders = main.getByRole("table", { name: "Órdenes de compra" });
  const po = orders.getByRole("row").filter({ hasText: "$ 45.000" });
  await expect(po).toHaveCount(1);
  await expect(po).toContainText(d.vendor_name);
  await expect(po.getByRole("cell").nth(2)).toHaveText("Borrador");
  const after = (await api.get(`/api/v1/purchasing/internal-requisitions/${req.id}`)).lines[0];
  expect(after.line_status).toBe("IN_PURCHASE");
  expect(after.lineage.map((e: any) => e.kind)).toEqual(expect.arrayContaining(["transfer", "consolidation", "purchase_order"]));
});
