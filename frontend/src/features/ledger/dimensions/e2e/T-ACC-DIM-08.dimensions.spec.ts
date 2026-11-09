// T-ACC-DIM-08 (e2e, gate G1): UI table↔tree toggle, create group+leaf, move, archive in each of the 4 tabs;
// keyboard tree navigation (role=tree). Owner P05.
//
// Self-contained so it runs under the config-less `make test-ids` Playwright call: it starts the P08 Vite dev server
// itself (127.0.0.1, free port), opens the harness that mounts the real DimensionsPage, and serves
// /api/v1/ledger/dimensions/* from the stateful contract fake (contractFake.ts). The live API behind it is proven by
// T-ACC-DIM-01…07 on PostgreSQL. Browser: system Chrome ($CHROME_PATH), no download.
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import type { ViteDevServer } from "vite";
import { DimensionContractFake } from "./contractFake";

const here = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(here, "../../../../..");
const COMPANY = "0190f000-0000-7000-8000-0000000c0a01";
const TABS = [
  { tab: "centros-costo", label: "Centros de costo", prefix: "CC" },
  { tab: "unidades-negocio", label: "Unidades de negocio", prefix: "UN" },
  { tab: "auxiliares-abiertos", label: "Auxiliares abiertos", prefix: "AUX" },
  { tab: "items-contables", label: "Ítems contables", prefix: "IT" },
] as const;

const executablePath = process.env.CHROME_PATH || undefined;
test.use({ launchOptions: executablePath ? { executablePath } : {}, locale: "es-CO", timezoneId: "America/Bogota" });
test.describe.configure({ mode: "serial" });

let server: ViteDevServer;
let baseUrl = "";

test.beforeAll(async () => {
  const { createServer } = await import("vite");
  server = await createServer({
    configFile: resolve(frontendDir, "vite.config.ts"),
    root: frontendDir,
    logLevel: "error",
    server: { host: "127.0.0.1", port: 0, strictPort: false, hmr: false },
  });
  await server.listen();
  const url = server.resolvedUrls?.local[0];
  if (!url) throw new Error("vite did not report a local URL");
  baseUrl = url.replace(/\/$/, "");
});

test.afterAll(async () => {
  await server?.close();
});

async function open(page: Page, fake: DimensionContractFake, tab: string) {
  await page.route("**/api/v1/ledger/dimensions/**", async (route) => {
    const req = route.request();
    const raw = req.postData();
    const res = fake.handle({ method: req.method(), url: req.url(), headers: req.headers(), body: raw ? JSON.parse(raw) : null });
    await route.fulfill({ status: res.status, contentType: res.contentType, body: res.status === 204 ? "" : JSON.stringify(res.body) });
  });
  await page.goto(`${baseUrl}/src/features/ledger/dimensions/e2e/harness.html?tab=${tab}&company=${COMPANY}`);
  await expect(page.getByRole("heading", { name: "Dimensiones contables" })).toBeVisible();
}

async function create(page: Page, kind: "Nuevo agrupador" | "Nuevo detalle", code: string, description: string, parentPath?: string) {
  await page.getByRole("button", { name: kind }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Código").fill(code);
  await dialog.getByLabel("Descripción").fill(description);
  if (parentPath) {
    const select = dialog.getByLabel("Agrupador padre");
    const options = await select.locator("option").allTextContents();
    const hit = options.find((o) => o.startsWith(`${parentPath} —`));
    if (!hit) throw new Error(`parent ${parentPath} not offered: ${options.join(" | ")}`);
    await select.selectOption({ label: hit });
  }
  await dialog.getByRole("button", { name: "Guardar" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("[data-nx-dim-status]")).toHaveText(`Se creó ${code}.`);
}

for (const t of TABS) {
  test(`T-ACC-DIM-08 ${t.label}: table↔tree toggle, create group+leaf, move, archive`, async ({ page }) => {
    const fake = new DimensionContractFake(COMPANY);
    await open(page, fake, TABS[0].tab);

    // Deep-linkable ARIA tablist with the four tabs; select this tab.
    const tablist = page.getByRole("tablist", { name: "Dimensiones contables" });
    await expect(tablist.getByRole("tab")).toHaveCount(4);
    await tablist.getByRole("tab", { name: t.label }).click();
    await expect(tablist.getByRole("tab", { name: t.label })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText(/Aún no hay/)).toBeVisible();

    const group = `${t.prefix}-G1`;
    const group2 = `${t.prefix}-G2`;
    const leaf = `${t.prefix}-G1-01`;
    await create(page, "Nuevo agrupador", group, "Administración (SINTÉTICO)");
    await create(page, "Nuevo agrupador", group2, "Operaciones (SINTÉTICO)");
    await create(page, "Nuevo detalle", leaf, "Contabilidad (SINTÉTICO)", group);

    // Tree view (default): WAI-ARIA tree, the leaf is under its group with Agrupador/Detalle badges.
    const tree = page.getByRole("tree", { name: t.label });
    await expect(tree).toBeVisible();
    const leafItem = tree.getByRole("treeitem").filter({ hasText: leaf });
    await expect(leafItem).toHaveAttribute("aria-level", "2");
    await expect(leafItem).toContainText("Detalle");
    await expect(tree.getByRole("treeitem").filter({ hasText: group }).first()).toContainText("Agrupador");

    // Toggle to the table view: same dataset (3 rows), path column; back to the tree.
    await page.getByRole("button", { name: "Tabla" }).click();
    await expect(page.getByRole("button", { name: "Tabla" })).toHaveAttribute("aria-pressed", "true");
    const table = page.getByRole("table", { name: t.label });
    await expect(table).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(4); // header + 3 nodes
    await expect(table.getByRole("row").filter({ hasText: leaf })).toContainText(group);

    // Move the leaf under the second group (from the table view).
    await table.getByRole("button", { name: `Mover ${leaf}` }).click();
    const move = page.getByRole("dialog");
    const options = await move.getByLabel("Nuevo agrupador padre").locator("option").allTextContents();
    await move.getByLabel("Nuevo agrupador padre").selectOption({ label: options.find((o) => o.startsWith(`${group2} —`))! });
    await move.getByRole("button", { name: "Mover" }).click();
    await expect(move).toBeHidden();
    await expect(page.locator("[data-nx-dim-status]")).toHaveText(`Se movió ${leaf} a ${group2}/${leaf}.`);
    expect(fake.nodes.size).toBe(3);
    expect([...fake.nodes.values()].find((n) => n.code === leaf)?.path).toBe(`${group2}/${leaf}`);

    // Archive the leaf: usage count shown, archived row keeps a text label (not only colour).
    await table.getByRole("button", { name: `Archivar ${leaf}` }).click();
    const archive = page.getByRole("dialog");
    await expect(archive.locator("[data-nx-usage]")).toHaveText("Sin movimientos contabilizados.");
    await archive.getByLabel("Motivo").fill("Reorganización (SINTÉTICO)");
    await archive.getByRole("button", { name: "Archivar" }).click();
    await expect(archive).toBeHidden();
    await expect(page.locator("[data-nx-dim-status]")).toHaveText(`Se archivó ${leaf}.`);
    await expect(table.getByRole("row").filter({ hasText: leaf })).toContainText("Archivado");
    await expect(table.getByRole("button", { name: `Desarchivar ${leaf}` })).toBeVisible();

    await page.getByRole("button", { name: "Árbol" }).click();
    await expect(page.getByRole("tree", { name: t.label }).getByRole("treeitem").filter({ hasText: leaf })).toContainText("Archivado");

    // Every write carried an Idempotency-Key and the active company.
    const writes = fake.log.filter((r) => r.method !== "GET");
    expect(writes.length).toBe(5);
    for (const w of writes) {
      expect(w.headers["idempotency-key"]).toBeTruthy();
      expect(w.headers["x-company-id"]).toBe(COMPANY);
    }
  });
}

test("T-ACC-DIM-08 keyboard tree navigation (role=tree)", async ({ page }) => {
  const fake = new DimensionContractFake(COMPANY);
  await open(page, fake, "auxiliares-abiertos");
  await create(page, "Nuevo agrupador", "PRY", "Proyectos (SINTÉTICO)");
  await create(page, "Nuevo detalle", "PRY-01", "Proyecto uno (SINTÉTICO)", "PRY");
  await create(page, "Nuevo detalle", "PRY-02", "Proyecto dos (SINTÉTICO)", "PRY");
  await create(page, "Nuevo agrupador", "ZZZ", "Otros (SINTÉTICO)");

  const tree = page.getByRole("tree", { name: "Auxiliares abiertos" });
  await tree.focus();
  const active = async () => {
    const id = await tree.getAttribute("aria-activedescendant");
    return id ? page.locator(`[id="${id}"]`) : null;
  };
  const group = tree.getByRole("treeitem").filter({ hasText: "Proyectos" });
  await expect(group).toHaveAttribute("aria-expanded", "true");
  await expect(await active()).toContainText("PRY");

  await page.keyboard.press("ArrowLeft"); // collapse
  await expect(group).toHaveAttribute("aria-expanded", "false");
  await expect(tree.getByRole("treeitem")).toHaveCount(2);
  await page.keyboard.press("ArrowRight"); // expand
  await expect(group).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("ArrowRight"); // into first child
  await expect(await active()).toContainText("PRY-01");
  await expect((await active())!).toHaveAttribute("aria-level", "2");
  await page.keyboard.press("ArrowDown");
  await expect(await active()).toContainText("PRY-02");
  await page.keyboard.press("ArrowLeft"); // to parent
  await expect(await active()).toContainText("Proyectos");
  await page.keyboard.press("End");
  await expect(await active()).toContainText("ZZZ");
  await page.keyboard.press("Home");
  await expect(await active()).toContainText("Proyectos");
  await page.keyboard.press("Enter");
  await expect(group).toHaveAttribute("aria-selected", "true");
  for (const item of await tree.getByRole("treeitem").all()) {
    await expect(item).toHaveAttribute("aria-level", /[12]/);
    await expect(item).toHaveAttribute("aria-posinset", /\d/);
    await expect(item).toHaveAttribute("aria-setsize", /\d/);
  }
});
