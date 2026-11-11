// T-INV-XFORM-09 (e2e, gate G3): UI: one transformation of each type (presentation change, portioning, manual)
// end-to-end incl. simulate, approve, post, print, reverse.
// World: tests/cross_module/test_xm_e2e_production.py::world_xform (gate world: real P09 inventory/transformations,
// P04 policy, P05 ledger). Pages: frontend/src/features/inventory/transformations/{TransformationsPage,
// PortioningTemplatesPage}.tsx (delivered, not registered in the inventory descriptor → augment).
// Users: bodega creates / posts / reverses, jefe_inv approves (segregation of duties, D-023).
import type { Page } from "@playwright/test";
import { expect, expectLabelledControls, tabTo, test, type App } from "./support/stack";
import { expectStock, message, rowAction } from "./support/production";

const XF = "/m/inventario/produccion/transformaciones";

function row(page: Page, number: string) {
  return page.getByRole("main").getByRole("table", { name: "Transformaciones" }).getByRole("row").filter({ hasText: number });
}

async function openTab(app: App, tab: "Presentación" | "Porcionado" | "Manual", user?: string): Promise<void> {
  await app.open(XF, user);
  await expect(app.page.getByRole("heading", { name: "Transformaciones", exact: true })).toBeVisible();
  await app.page.getByRole("tab", { name: tab, exact: true }).click();
  await expect(app.page.getByRole("tab", { name: tab, exact: true })).toHaveAttribute("aria-selected", "true");
}

async function expectStatus(page: Page, number: string, status: string): Promise<void> {
  await expect(message(page, "Transformación")).toHaveText(`Transformación ${number}: ${status.split(" ·")[0]}`);
  await expect(row(page, number).getByRole("cell").nth(2)).toHaveText(status);
}

/** Print: the UI must offer a print / PDF action for the document; there is none (and no PDF endpoint). */
async function print(app: App, number: string, id: string): Promise<void> {
  await expect(row(app.page, number).getByRole("button", { name: new RegExp(`^(Imprimir|PDF) ${number}$`) })).toHaveCount(0);
  await app.gap(
    "DEF-P22-043",
    "Transformaciones: no print/PDF action for a transformation document (no button, no /transformations/{id}/pdf endpoint; only the JSON document)",
    async () => {
      const doc = await app.api().get(`/api/v1/inventory/transformations/${id}`);
      expect(doc.number).toBe(number);
    },
  );
}

test("T-INV-XFORM-09 presentation, portioning and manual transformations: simulate, approve, post, print, reverse (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const items = d.items as Record<string, string>;
  const label = (code: string) => `${code} — ${(d.names as Record<string, string>)[code]}`;
  const api = app.api("bodega");
  const create = (body: unknown) => api.post("/api/v1/inventory/transformations", body);

  // ================================================================ 1. PRESENTATION (unpack 2 × CJ12 → 24 UND)
  await openTab(app, "Presentación");
  await expectLabelledControls(page);
  // the form has no presentation selector (lines carry only role / item / quantity / actual weight), so a repack
  // (input and output presentations must differ) cannot be created in the UI
  const firstLine = page.getByRole("form", { name: "Nueva transformación" }).getByRole("group", { name: "Línea 1" });
  await expect(firstLine.getByRole("combobox")).toHaveCount(2); // Rol, Producto — no Presentación
  const repack = await app.gap(
    "DEF-P22-041",
    "Transformaciones: the document form has no presentation / packages fields, so a PRESENTATION (repack) document cannot be created in the UI",
    async () =>
      create({
        transformation_type: "PRESENTATION",
        warehouse_id: d.warehouse_id,
        lines: [
          { line_no: 1, role: "INPUT", item_id: items.AZUCAR, presentation_id: d.presentation_id, qty_packages: "2" },
          { line_no: 2, role: "OUTPUT", item_id: items.AZUCAR, quantity: "24" },
        ],
      }),
  );
  await openTab(app, "Presentación");
  await expect(row(page, repack.number).getByRole("cell").nth(2)).toHaveText("DRAFT");
  // simulate: base 24 → 24, value-neutral within one valuation account → no journal
  await page.getByRole("button", { name: `Simular ${repack.number}`, exact: true }).click();
  const sim = page.getByRole("region", { name: "Simulación" });
  await expect(sim).toContainText("Base: 24 → 24");
  await expect(sim).toContainText("Dentro de tolerancia");
  // DEF-P22-044 (product bug, not a missing UI): the :simulate preview of a repack values the input but not the
  // output ("no cuadra", "salida 0") and shows a one-sided journal "Cr INVENTORY …" although the post books nothing.
  // Soft assertions so the remaining steps still run; the spec fails on them (never hidden as a gap).
  await expect.soft(sim, "DEF-P22-044 repack simulation balances").toContainText("Balance de valor: cuadra · Base: 24 → 24");
  await expect.soft(sim.getByRole("list", { name: "Asiento previsto" }), "DEF-P22-044 repack simulation shows no journal").toHaveCount(0);
  // approve: submitting a repack needs no approval → APPROVED directly (keyboard: Enter on "Enviar")
  const submitRepack = page.getByRole("button", { name: `Enviar ${repack.number}`, exact: true });
  await submitRepack.focus();
  await page.keyboard.press("Enter");
  await expectStatus(page, repack.number, "APPROVED");
  await rowAction(page, `Contabilizar ${repack.number}`);
  await expectStatus(page, repack.number, "POSTED");
  await print(app, repack.number, repack.id);
  await expectStock(app, label("AZUCAR"), { canonical: "24", loose: "24", presentation: { code: "CJ12", packages: "0" } });
  await openTab(app, "Presentación");
  await rowAction(page, `Reversar ${repack.number}`, "error de reempaque (SINTÉTICO)");
  await expectStatus(page, repack.number, "REVERSED");
  await expectStock(app, label("AZUCAR"), { canonical: "24", loose: "0", presentation: { code: "CJ12", packages: "2" } });

  // ================================================================ 2. PORTIONING with a template (out of tolerance)
  await app.open("/m/inventario/produccion/plantillas-porcionado");
  await expect(page.getByRole("heading", { name: "Plantillas de porcionado", exact: true })).toBeVisible();
  const tpl = page.getByRole("form", { name: "Nueva plantilla" });
  await tpl.getByLabel("Código", { exact: true }).fill("DESPOSTE-RES");
  await tpl.getByLabel("Nombre", { exact: true }).fill("Desposte res (SINTÉTICO)");
  await tpl.getByLabel(/^Insumo/).selectOption({ label: "RES-CANAL" });
  await tpl.getByLabel("Merma %", { exact: true }).fill("5");
  await tpl.getByLabel("Tolerancia %", { exact: true }).fill("2");
  const outputs: Array<[string, string, string]> = [["LOMO", "32", "50"], ["MOLIDA", "41", "40"], ["HUESO", "22", "10"]];
  for (const [i, [code, yieldPct, share]] of outputs.entries()) {
    if (i > 0) await tpl.getByRole("button", { name: "Agregar salida" }).click();
    const g = tpl.getByRole("group", { name: `Salida ${i + 1}` });
    await g.getByLabel("Producto", { exact: true }).selectOption({ label: code });
    await g.getByLabel("Rendimiento esperado %", { exact: true }).fill(yieldPct);
    await g.getByLabel("Participación en costo %", { exact: true }).fill(share);
  }
  await expect(page.getByRole("main").getByRole("status").filter({ hasText: "Rendimientos + merma" })).toHaveText("Rendimientos + merma: 100 %");
  await expectLabelledControls(page);
  await tabTo(page, tpl.getByRole("button", { name: "Guardar" }));
  await page.keyboard.press("Enter");
  await expect(message(page, "Plantilla")).toHaveText("Plantilla DESPOSTE-RES creada");
  const tplRow = page.getByRole("row").filter({ hasText: "DESPOSTE-RES" });
  await expect(tplRow).toContainText("LOMO 32 % · MOLIDA 41 % · HUESO 22 %");
  await expect(tplRow).toContainText("± 2 %");

  // the transformation form has no template selector: a template-based portioning cannot be created in the UI
  await openTab(app, "Porcionado");
  await expect(page.getByRole("form", { name: "Nueva transformación" }).getByLabel(/Plantilla/)).toHaveCount(0);
  const portion = await app.gap(
    "DEF-P22-042",
    "Transformaciones: the document form has no portioning-template selector (template_id), so a template-based PORTIONING cannot be created in the UI",
    async () => {
      const templates = (await api.get("/api/v1/inventory/portioning-templates")).items as any[];
      const t = templates.find((x) => x.code === "DESPOSTE-RES");
      expect(t, "template created in the UI is returned by the API").toBeTruthy();
      return create({
        transformation_type: "PORTIONING",
        warehouse_id: d.warehouse_id,
        template_id: t.id,
        lines: [
          { line_no: 1, role: "INPUT", item_id: items["RES-CANAL"], quantity: "10" },
          { line_no: 2, role: "OUTPUT", item_id: items.LOMO, quantity: "2.9", actual_weight: "2.9" },
          { line_no: 3, role: "OUTPUT", item_id: items.MOLIDA, quantity: "4.5", actual_weight: "4.5" },
          { line_no: 4, role: "OUTPUT", item_id: items.HUESO, quantity: "2.1", actual_weight: "2.1" },
          { line_no: 5, role: "WASTE", item_id: items["RES-CANAL"], quantity: "0.5" },
        ],
      });
    },
  );
  await openTab(app, "Porcionado");
  await page.getByRole("button", { name: `Simular ${portion.number}`, exact: true }).click();
  // 10 kg × 20 000 = 200 000; shares 50/40/10 → LOMO 100 000, MOLIDA 80 000, HUESO 20 000; 0.5 kg normal waste absorbed
  await expect(sim).toContainText("Balance de valor: cuadra · Base: 10 → 9,5");
  await expect(sim).toContainText("Valor entrada 200.000 · salida 200.000 · merma gasto 0");
  await expect(sim).toContainText("Requiere aprobación:");
  await expect(sim).toContainText("rendimiento LOMO");
  await expect(sim).toContainText("rendimiento MOLIDA");
  await expect(sim).not.toContainText("rendimiento HUESO");
  const journal = sim.getByRole("list", { name: "Asiento previsto" });
  await expect(journal.getByRole("listitem")).toHaveText(["Cr INVENTORY 200.000", "Db INVENTORY_CARNICOS 200.000"]);
  // out of tolerance: posting the draft is refused, nothing consumed
  await rowAction(page, `Contabilizar ${portion.number}`);
  await expect(message(page, "transformation_yield_out_of_tolerance")).toBeVisible();
  await rowAction(page, `Enviar ${portion.number}`);
  await expectStatus(page, portion.number, "SUBMITTED · requiere aprobación");
  // SoD: the requester cannot approve
  await rowAction(page, `Aprobar ${portion.number}`, "aprobación propia");
  await expect(message(page, "sod_violation")).toBeVisible();
  await expect(row(page, portion.number).getByRole("cell").nth(2)).toHaveText("SUBMITTED · requiere aprobación");
  // the approver approves with a reason (D-015)
  await openTab(app, "Porcionado", "jefe_inv");
  await rowAction(page, `Aprobar ${portion.number}`, "canal magra, verificado (SINTÉTICO)");
  await expectStatus(page, portion.number, "APPROVED · requiere aprobación");
  await openTab(app, "Porcionado", "bodega");
  await rowAction(page, `Contabilizar ${portion.number}`);
  await expectStatus(page, portion.number, "POSTED · requiere aprobación");
  await expect(row(page, portion.number).getByRole("cell").nth(3)).toHaveText("200.000 COP");
  await print(app, portion.number, portion.id);
  await expectStock(app, label("RES-CANAL"), { canonical: "0" });
  await expectStock(app, label("LOMO"), { canonical: "2,9" });
  await openTab(app, "Porcionado");
  await rowAction(page, `Reversar ${portion.number}`, "pesaje erróneo (SINTÉTICO)");
  await expectStatus(page, portion.number, "REVERSED · requiere aprobación");
  await expectStock(app, label("RES-CANAL"), { canonical: "10" });
  await expectStock(app, label("LOMO"), { canonical: "0" });

  // ================================================================ 3. MANUAL created in the UI (above the threshold)
  await openTab(app, "Manual");
  const form = page.getByRole("form", { name: "Nueva transformación" });
  await form.getByLabel(/^Bodega/).selectOption({ label: d.warehouse_code });
  const lines: Array<["INPUT" | "OUTPUT", string, string]> = [
    ["INPUT", "MAN-A", "10"],
    ["INPUT", "MAN-B", "5"],
    ["OUTPUT", "MAN-C", "12"],
    ["OUTPUT", "MAN-D", "3"],
  ];
  for (const [i, [role, code, qty]] of lines.entries()) {
    if (i > 0) await form.getByRole("button", { name: "Agregar línea" }).click();
    const g = form.getByRole("group", { name: `Línea ${i + 1}` });
    await g.getByLabel("Rol", { exact: true }).selectOption(role);
    await g.getByLabel("Producto", { exact: true }).selectOption({ label: code });
    await g.getByLabel("Cantidad", { exact: true }).fill(qty);
  }
  await expect(form.getByRole("status")).toHaveText("Balance de cantidades: entrada 15 = salida 15 + merma 0 ✓");
  await expectLabelledControls(page);
  await tabTo(page, form.getByRole("button", { name: "Crear borrador" }));
  await page.keyboard.press("Enter");
  const created = message(page, "Transformación");
  await expect(created).toHaveText(/^Transformación TRF-\S+ creada \(DRAFT\)$/);
  const manualNo = (await created.textContent())!.match(/(TRF-\S+)/)![1]!;
  await expect(row(page, manualNo).getByRole("cell").nth(2)).toHaveText("DRAFT");
  // simulate: 10 × 80 000 + 5 × 60 000 = 1 100 000 > COP 1 000 000 → approval; by quantity MAN-C 880 000, MAN-D 220 000
  await page.getByRole("button", { name: `Simular ${manualNo}`, exact: true }).click();
  await expect(sim).toContainText("Balance de valor: cuadra · Base: 15 → 15");
  await expect(sim).toContainText("Valor entrada 1.100.000 · salida 1.100.000 · merma gasto 0");
  await expect(sim).toContainText("Requiere aprobación:");
  await expect(sim).toContainText("supera el umbral");
  await expect(journal.getByRole("listitem")).toHaveText(["Cr INVENTORY 880.000", "Db INVENTORY_CARNICOS 880.000"]);
  await rowAction(page, `Enviar ${manualNo}`);
  await expectStatus(page, manualNo, "SUBMITTED · requiere aprobación");
  await openTab(app, "Manual", "jefe_inv");
  await rowAction(page, `Aprobar ${manualNo}`, "ensamble autorizado (SINTÉTICO)");
  await expectStatus(page, manualNo, "APPROVED · requiere aprobación");
  await openTab(app, "Manual", "bodega");
  await rowAction(page, `Contabilizar ${manualNo}`);
  await expectStatus(page, manualNo, "POSTED · requiere aprobación");
  await expect(row(page, manualNo).getByRole("cell").nth(3)).toHaveText("1.100.000 COP");
  const manualId = ((await api.get("/api/v1/inventory/transformations", { transformation_type: "MANUAL", limit: "100" })).items as any[]).find((o) => o.number === manualNo).id;
  await print(app, manualNo, manualId);
  await expectStock(app, label("MAN-A"), { canonical: "0" });
  await expectStock(app, label("MAN-C"), { canonical: "12" });
  await openTab(app, "Manual");
  await rowAction(page, `Reversar ${manualNo}`, "ensamble anulado (SINTÉTICO)");
  await expectStatus(page, manualNo, "REVERSED · requiere aprobación");
  await expectStock(app, label("MAN-A"), { canonical: "10" });
  await expectStock(app, label("MAN-C"), { canonical: "0" });
});
