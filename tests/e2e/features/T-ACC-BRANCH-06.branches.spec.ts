// T-ACC-BRANCH-06 (e2e, gate G3): UI: Administración › Empresas › Sucursales list/create/edit/archive with dependency
// list; keyboard + labels. World: tests/cross_module/test_xm_e2e_branches.py (real P04 branches API, P09 warehouse
// referencing the branch so the archive is refused with its dependency list).
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";

test("T-ACC-BRANCH-06 Sucursales list/create/edit/archive with dependency list (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const api = app.api("admin");

  // navigation through the shell: Administración › Empresas y sucursales › tab Sucursales (keyboard)
  await app.open("/m/administracion/empresas");
  const tab = page.getByRole("tab", { name: "Sucursales" });
  await expect(tab).toBeVisible();
  await tabTo(page, page.getByRole("tab", { selected: true }));
  await page.keyboard.press("ArrowRight");
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\/m\/administracion\/empresas\/sucursales$/);
  await expectLabelledControls(page);

  // the Sucursales page itself: a list of the company's branches with a create action
  const panel = page.getByRole("tabpanel", { name: "Sucursales" });
  const list = panel.getByRole("table");
  const hasPage = (await list.count()) > 0 && (await panel.getByRole("button", { name: /Nueva sucursal/ }).count()) > 0;
  if (!hasPage) {
    await expect(panel).toContainText("Módulo aún no disponible");
    await app.gap("DEF-P22-011", "Administración › Empresas › Sucursales has no page (P04 delivered only the API client features/iam/api.ts): no list, create, edit or archive UI", async () => {
      const listed = await api.get("/api/v1/iam/branches", { sort: "code" });
      expect(listed.items.map((b: any) => b.code)).toContain("BOG");
      const created = await api.post(`/api/v1/iam/companies/${d.company_id}/branches`, { code: "CAL", name: "Sucursal Cali (SINTÉTICA)", is_head_office: false });
      expect(created.status).toBe("ACTIVE");
      const res = await page.request.get(`${app.world.api_origin}/api/v1/iam/branches/${created.id}`, {
        headers: { Authorization: `Bearer ${app.world.tokens.admin}`, "X-Company-Id": d.company_id },
      });
      const etag = res.headers()["etag"];
      const edited = await page.request.patch(`${app.world.api_origin}/api/v1/iam/branches/${created.id}`, {
        headers: { Authorization: `Bearer ${app.world.tokens.admin}`, "X-Company-Id": d.company_id, "If-Match": etag, "Idempotency-Key": crypto.randomUUID() },
        data: { name: "Sucursal Cali Norte (SINTÉTICA)" },
      });
      expect(edited.status()).toBe(200);
      // archive refused while the warehouse references BOG: the dependency list names it
      const refused = await page.request.post(`${app.world.api_origin}/api/v1/iam/branches/${d.branch_id}:archive`, {
        headers: { Authorization: `Bearer ${app.world.tokens.admin}`, "X-Company-Id": d.company_id, "Idempotency-Key": crypto.randomUUID() },
        data: {},
      });
      expect(refused.status()).toBe(409);
      expect(JSON.stringify(await refused.json())).toContain(d.warehouse_code);
      const archived = await api.post(`/api/v1/iam/branches/${created.id}:archive`);
      expect(archived.status).toBe("ARCHIVED");
    });
    return;
  }
  // (when the page exists, this spec must be extended to drive list/create/edit/archive through it)
  throw new Error("Sucursales page now exists: extend T-ACC-BRANCH-06 to drive it in the UI and remove DEF-P22-011");
});
