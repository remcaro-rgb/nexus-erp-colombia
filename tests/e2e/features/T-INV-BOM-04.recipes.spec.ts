// T-INV-BOM-04 (e2e, gate G3): UI: create recipe v1, activate, copy to v2 with new dates.
// World: tests/cross_module/test_xm_e2e_production.py::world_bom (gate world: real P09 inventory/BOM, P04 policy, P05 ledger).
// Page: frontend/src/features/inventory/bom/RecipesPage.tsx (delivered, not registered in the inventory descriptor → augment).
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";
import { message } from "./support/production";

test("T-INV-BOM-04 create recipe v1, activate, copy to v2 with new dates (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");

  // 1. Inventario › Producción › Recetas / BOM: new recipe v1 valid from 2026-01-01 with yield and component scrap
  await app.open("/m/inventario/produccion/recetas");
  await expect(page.getByRole("heading", { name: "Recetas y listas de materiales" })).toBeVisible();
  const form = page.getByRole("form", { name: "Nueva receta" });
  await form.getByLabel("Código", { exact: true }).fill("REC-PAN");
  await form.getByLabel("Nombre", { exact: true }).fill("Pan de molde (SINTÉTICO)");
  await form.getByLabel(/^Producto/).selectOption({ label: d.output_code });
  await form.getByLabel("Salida por lote", { exact: true }).fill("10");
  await form.getByLabel("Rendimiento %", { exact: true }).fill("100");
  await form.getByLabel("Vigente desde", { exact: true }).fill("2026-01-01");
  const c1 = form.getByRole("group", { name: "Componente 1" });
  await c1.getByLabel("Componente", { exact: true }).selectOption({ label: d.harina_code });
  await c1.getByLabel("Cantidad por lote", { exact: true }).fill("5");
  await c1.getByLabel("Desperdicio %", { exact: true }).fill("20");
  await form.getByRole("button", { name: "Agregar componente" }).click();
  const c2 = form.getByRole("group", { name: "Componente 2" });
  await c2.getByLabel("Componente", { exact: true }).selectOption({ label: d.levadura_code });
  await c2.getByLabel("Cantidad por lote", { exact: true }).fill("1");
  await c2.getByLabel("Desperdicio %", { exact: true }).fill("0");
  await expectLabelledControls(page);
  // keyboard: the save action is reachable with Tab and fires with Enter
  await c2.getByLabel("Desperdicio %", { exact: true }).focus();
  await tabTo(page, form.getByRole("button", { name: "Guardar borrador" }));
  await page.keyboard.press("Enter");
  await expect(message(page, "Receta")).toHaveText("Receta REC-PAN v1 creada en borrador");
  const versions = main.getByRole("table", { name: "Versiones de recetas" });
  const v1 = versions.getByRole("row").filter({ hasText: "v1" });
  await expect(v1.getByRole("cell").nth(0)).toHaveText("REC-PAN");
  await expect(v1.getByRole("cell").nth(2)).toHaveText("DRAFT");
  await expect(v1.getByRole("cell").nth(3)).toHaveText("2026-01-01 – ∞");
  await expect(v1.getByRole("cell").nth(4)).toHaveText("100 %");

  // 2. activate v1 (server validation: no cycle, active components, yield in (0,100]) — keyboard: Space on the button
  const activate1 = page.getByRole("button", { name: "Activar REC-PAN v1", exact: true });
  await activate1.focus();
  await page.keyboard.press("Space");
  await expect(message(page, "Receta")).toHaveText("Receta REC-PAN v1: ACTIVE");
  await expect(v1.getByRole("cell").nth(2)).toHaveText("ACTIVE");

  // explosion preview with cost: 10 loaves = HARINA 5 × 1.2 (20 % scrap) = 6, LEVADURA 1; 6 × 2 500 + 1 × 800 = 15 800
  const preview = page.getByRole("group", { name: "Vista previa de explosión" });
  await preview.getByLabel("Cantidad", { exact: true }).fill("10");
  await preview.getByLabel("Fecha", { exact: true }).fill("2026-11-09");
  await page.getByRole("button", { name: "Explosionar REC-PAN v1", exact: true }).click();
  const explosion = page.getByRole("table", { name: "Explosión de la receta" });
  await expect(explosion.getByRole("row").filter({ hasText: d.harina_code }).getByRole("cell").nth(1)).toHaveText("6");
  await expect(explosion.getByRole("row").filter({ hasText: d.levadura_code }).getByRole("cell").nth(1)).toHaveText("1");
  await expect(explosion.getByRole("row").filter({ hasText: "Costo total" })).toContainText("15.800 COP");

  // 3. copy to v2 with NEW dates (2027-01-01 … 2027-12-31). The page offers a "Nueva versión" button, but it always
  //    copies with valid_from = the browser's today and no valid_to: there is no date input for the new version and no
  //    editor for an existing draft, so the dates of v2 cannot be chosen in the UI.
  await expect(page.getByRole("button", { name: "Nueva versión REC-PAN v1", exact: true })).toBeVisible();
  const api = app.api();
  const boms = (await api.get("/api/v1/inventory/boms", { limit: "200" })).items as any[];
  const bomV1 = boms.find((b) => b.code === "REC-PAN" && b.version_no === 1);
  expect(bomV1, "recipe v1 created in the UI is returned by the API").toBeTruthy();
  await app.gap(
    "DEF-P22-040",
    "Recetas: 'Nueva versión' copies with a hard-coded valid_from = today and no valid_to; no UI to give the new version its dates",
    async () => api.post(`/api/v1/inventory/boms/${bomV1.id}:copy-version`, { valid_from: "2027-01-01", valid_to: "2027-12-31" }),
  );
  await app.open("/m/inventario/produccion/recetas");
  const v2 = versions.getByRole("row").filter({ hasText: "v2" });
  await expect(v2.getByRole("cell").nth(0)).toHaveText("REC-PAN");
  await expect(v2.getByRole("cell").nth(2)).toHaveText("DRAFT");
  await expect(v2.getByRole("cell").nth(3)).toHaveText("2027-01-01 – 2027-12-31");

  // 4. activate v2 in the UI: v1 (open-ended) is superseded the day before v2 starts; one ACTIVE version per date
  await page.getByRole("button", { name: "Activar REC-PAN v2", exact: true }).click();
  await expect(message(page, "Receta")).toHaveText("Receta REC-PAN v2: ACTIVE");
  await expect(v2.getByRole("cell").nth(2)).toHaveText("ACTIVE");
  await expect(v1.getByRole("cell").nth(2)).toHaveText("ACTIVE");
  await expect(v1.getByRole("cell").nth(3)).toHaveText("2026-01-01 – 2026-12-31");
  // v2 explodes with the same components (copied lines)
  await preview.getByLabel("Cantidad", { exact: true }).fill("10"); // the page was reloaded after the gap step
  await preview.getByLabel("Fecha", { exact: true }).fill("2027-03-01");
  await page.getByRole("button", { name: "Explosionar REC-PAN v2", exact: true }).click();
  await expect(explosion.getByRole("row").filter({ hasText: d.harina_code }).getByRole("cell").nth(1)).toHaveText("6");
  await expectLabelledControls(page);
});
