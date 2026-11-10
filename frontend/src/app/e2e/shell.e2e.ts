// P08 shell smoke e2e (system Chrome). The API is replaced by SYNTHETIC test fixtures through page.route (no backend
// in this check; labelled, no real data). Asserts UI-CONTRACT §9 layout boxes and tokens for the shell frame.
import { expect, test, type Page } from "@playwright/test";
import registry from "../../../../docs/spec/ui/module-registry.json" with { type: "json" };

const perms = new Set<string>();
for (const m of registry.modules as Array<{ permission: string; submenus: Array<{ permission: string }>; dashboard?: { kpis: Array<{ permission: string }> } }>) {
  perms.add(m.permission);
  for (const s of m.submenus) perms.add(s.permission);
  for (const k of m.dashboard?.kpis ?? []) perms.add(k.permission);
}

async function mockApi(page: Page) {
  await page.route((u) => u.pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    switch (url.pathname) {
      case "/api/v1/ui/context":
        return json({
          tenant_id: "t-e2e",
          company_id: "c-1",
          companies: [{ id: "c-1", legal_name: "Demo Andina S.A.S.", nit: "900000001-1" }],
          period: { id: "p-1", start: "2026-11-01", end: "2026-11-30", status: "open" },
          permissions: [...perms],
          environment: "dev",
          brand: { name: "NexusERP", logo_path: null },
          user: { display_name: "Usuario Sintético" },
        });
      case "/api/v1/iam/me":
        return json({ id: "u-e2e", user_id: "u-e2e", permissions: [...perms], field_masks: {} });
      case "/api/v1/ui/modules":
        return json({ groups: registry.groups, modules: registry.modules });
      case "/api/health/ready":
        return json({ status: "ok" });
      case "/api/v1/workflow/notifications":
        return json({ results: [] });
      default:
        return json({ type: "about:blank", title: "Not Found", status: 404, code: "not_found", trace_id: "e2e" }, 404);
    }
  });
}

async function box(page: Page, region: string) {
  const b = await page.locator(`[data-nx-region="${region}"]`).first().boundingBox();
  if (!b) throw new Error(`no box for ${region}`);
  return b;
}

test.describe("P08 shell e2e", () => {
  test("T-P08-E2E layout boxes, tokens and toggles at 1440x900", async ({ page }) => {
    await mockApi(page);
    await page.goto("/m/inicio");
    await page.locator('[data-nx-region="sidebar"]').waitFor();
    await page.evaluate(() => document.fonts.ready);

    const header = await box(page, "header");
    const bar = await box(page, "module-bar");
    const sidebar = await box(page, "sidebar");
    expect(Math.round(header.height)).toBe(64);
    expect(Math.round(bar.height)).toBe(48);
    expect(Math.round(bar.y)).toBe(Math.round(header.y + header.height));
    expect(Math.round(sidebar.width)).toBe(256);
    expect(Math.round(bar.x)).toBe(Math.round(sidebar.x + sidebar.width));

    const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(font.startsWith('"Inter"') || font.startsWith("Inter")).toBe(true);
    const appBg = await page.locator('[data-nx-region="main"]').evaluate((e) => getComputedStyle(e).backgroundColor);
    expect(appBg).toBe("rgb(248, 250, 252)");
    const quickBg = await page.locator('[data-nx-control="quick-action"]').evaluate((e) => getComputedStyle(e).backgroundColor);
    expect(quickBg).toBe("rgb(79, 70, 229)");
    const radius = await page.locator('[data-kpi]').first().evaluate((e) => getComputedStyle(e).borderRadius);
    expect(radius).toBe("16px");

    // Collapse sidebar -> 80 px; open AI -> docked 320 px, center ends at its left edge; no horizontal scroll.
    await page.locator('[data-nx-control="sidebar-toggle"]').click();
    expect(Math.round((await box(page, "sidebar")).width)).toBe(80);
    const ai = page.locator('[data-nx-control="ai-toggle"]');
    await ai.click();
    await expect(ai).toHaveAttribute("aria-expanded", "true");
    const panel = await box(page, "ai-panel");
    expect(Math.round(panel.width)).toBe(320);
    const center = await box(page, "center-column");
    expect(Math.round(center.x + center.width)).toBe(Math.round(panel.x));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);

    // Ctrl+K opens the search dialog; Esc closes it and the AI toggle keeps aria-expanded.
    await page.keyboard.press("Control+k");
    await expect(page.getByRole("combobox", { name: /Escriba para buscar/ })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("combobox", { name: /Escriba para buscar/ })).toHaveCount(0);
  });

  test("T-P08-E2E dark theme tokens and portal shell geometry", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/portal/v1/me", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "pa", portal_kind: "supplier", display_name: "Cuenta Sintética" }) }));
    await page.route("**/api/portal/v1/context", (r) => r.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "pc", company_name: "Demo Andina S.A.S." }) }));
    await page.goto("/m/inicio");
    await page.locator('[data-nx-control="theme"]').click();
    const darkBg = await page.locator('[data-nx-region="main"]').evaluate((e) => getComputedStyle(e).backgroundColor);
    expect(darkBg).toBe("rgb(2, 6, 23)");
    const internalTokens = await page.locator("[data-nx-shell]").getAttribute("data-nx-tokens");
    await page.goto("/portal/proveedores");
    await page.locator('[data-nx-shell="portal"]').waitFor();
    expect(await page.locator("[data-nx-shell]").getAttribute("data-nx-tokens")).toBe(internalTokens);
    expect(Math.round((await box(page, "header")).height)).toBe(64);
    await expect(page.locator('[data-nx-control="ai-toggle"]')).toHaveCount(0);
  });
});
