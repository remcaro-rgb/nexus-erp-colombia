// T-PORTAL-CORE-04 (P08, unit, gate G1): Portal shell renders only the menus of its kind; no admin/AI panel;
// tokens identical to the internal shell.
import { describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { buildRoutes } from "@app/App";
import { fakeApi, ALL_MODULES } from "@app/test/fakeApi";
import { createApiClient } from "@lib/api/client";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { PORTALS } from "../registry";
import type { ThemeName } from "@ds/tokens";

function renderAt(path: string, opts: Parameters<typeof fakeApi>[0] = {}, theme: ThemeName = "light") {
  const api = fakeApi(opts);
  const router = createMemoryRouter(buildRoutes(createApiClient(api.fetcher)), { initialEntries: [path] });
  const utils = render(
    <DesignSystemProvider theme={theme}>
      <RouterProvider router={router} />
    </DesignSystemProvider>,
  );
  return { ...utils, api, router };
}

async function shellRoot(variant: "internal" | "portal"): Promise<HTMLElement> {
  return waitFor(() => {
    const el = document.querySelector<HTMLElement>(`[data-nx-shell="${variant}"]`);
    if (!el) throw new Error(`no ${variant} shell yet`);
    return el;
  });
}

const INTERNAL_LABELS = ALL_MODULES.map((m) => m.label);

describe("T-PORTAL-CORE-04 portal shell variant", () => {
  window.innerWidth = 1440;

  for (const def of PORTALS) {
    it(`T-PORTAL-CORE-04 ${def.kind} portal renders only its own menus`, async () => {
      renderAt(def.route, { portalKind: def.kind });
      await shellRoot("portal");
      const nav = await screen.findByRole("navigation", { name: "Menú del portal" });
      const links = within(nav).getAllByRole("link");
      expect(links.map((l) => l.textContent)).toEqual(def.menus.map((m) => m.label));
      expect(links.map((l) => l.getAttribute("href"))).toEqual(def.menus.map((m) => m.route));
      // Nothing of the other portal kinds, nothing internal.
      for (const other of PORTALS.filter((p) => p.kind !== def.kind)) {
        for (const m of other.menus) {
          if (!def.menus.some((x) => x.route === m.route)) expect(document.querySelector(`nav a[href="${m.route}"]`)).toBeNull();
        }
      }
      expect(document.querySelector('a[href^="/m/"]')).toBeNull();
      // Dashboard: the portal's 3–5 registry KPIs, honest not-available state (no provider yet), never numbers.
      await waitFor(() => expect(document.querySelectorAll("[data-kpi]").length).toBe(def.dashboard.kpis.length));
      expect(def.dashboard.kpis.length).toBeGreaterThanOrEqual(3);
      await waitFor(() => expect(document.querySelector('[data-kpi][data-state="loading"]')).toBeNull());
      for (const card of document.querySelectorAll("[data-kpi]")) expect(card.getAttribute("data-state")).toBe("not_available");
    });
  }

  it("T-PORTAL-CORE-04 no admin, no AI panel, no company switcher, no internal search", async () => {
    const def = PORTALS[0]!;
    renderAt(def.route, { portalKind: def.kind });
    const root = await shellRoot("portal");
    await screen.findByRole("navigation", { name: "Menú del portal" });
    expect(root.querySelector('[data-nx-control="ai-toggle"]')).toBeNull();
    expect(document.querySelector('[data-nx-region="ai-panel"]')).toBeNull();
    expect(screen.queryByRole("button", { name: /Asistente IA/ })).toBeNull();
    expect(root.querySelector('[data-nx-control="company"]')).toBeNull();
    expect(root.querySelector('[data-nx-control="search"]')).toBeNull();
    expect(screen.queryByText("Administración")).toBeNull();
    for (const label of INTERNAL_LABELS.filter((l) => !def.menus.some((m) => m.label === l))) expect(within(root).queryByRole("link", { name: label })).toBeNull();
    // What the portal top bar does have: brand, company name, notifications, theme, profile, DEV banner.
    expect(screen.getByRole("link", { name: /NexusERP — ir a Inicio/ })).toHaveAttribute("href", def.route);
    expect(root.querySelector("[data-nx-portal-company]")?.textContent).toBe("Demo Andina S.A.S.");
    expect(root.querySelector('[data-nx-control="notifications"]')).not.toBeNull();
    expect(root.querySelector('[data-nx-control="theme"]')).not.toBeNull();
    expect(root.querySelector('[data-nx-control="profile"]')).not.toBeNull();
    expect(root.querySelector('[data-nx-region="dev-banner"]')).not.toBeNull();
    expect(document.querySelectorAll('[data-nx-region="header"]')).toHaveLength(1);
  });

  it("T-PORTAL-CORE-04 an account of another kind gets no menus (portal_kind_mismatch)", async () => {
    const supplier = PORTALS.find((p) => p.kind === "supplier")!;
    renderAt(supplier.route, { portalKind: "customer" });
    await shellRoot("portal");
    expect(await screen.findByText("Su cuenta no tiene acceso a este portal.")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Menú del portal" })).toBeNull();
    for (const m of supplier.menus) expect(document.querySelector(`nav a[href="${m.route}"]`)).toBeNull();
    expect(document.querySelector("[data-kpi]")).toBeNull();
  });

  for (const theme of ["light", "dark"] as const) {
    it(`T-PORTAL-CORE-04 tokens identical to the internal shell (${theme})`, async () => {
      const internal = renderAt("/m/inicio", {}, theme);
      const iRoot = await shellRoot("internal");
      const iFp = iRoot.getAttribute("data-nx-tokens");
      const iStyle = iRoot.getAttribute("style");
      const iTheme = iRoot.getAttribute("data-nx-theme");
      internal.unmount();

      const def = PORTALS[1]!;
      renderAt(def.route, { portalKind: def.kind }, theme);
      const pRoot = await shellRoot("portal");
      expect(iTheme).toBe(theme);
      expect(pRoot.getAttribute("data-nx-theme")).toBe(theme);
      expect(iFp).toMatch(/^[0-9a-f]{8}$/);
      expect(pRoot.getAttribute("data-nx-tokens")).toBe(iFp);
      expect(pRoot.getAttribute("style")).toBe(iStyle);
      // Spot-check token values from docs/spec/ui/tokens.json.
      expect(pRoot.style.getPropertyValue("--nx-color-brand-primary")).toBe(theme === "light" ? "#4f46e5" : "#818cf8");
      expect(pRoot.style.getPropertyValue("--nx-color-app-bg")).toBe(theme === "light" ? "#f8fafc" : "#020617");
      expect(pRoot.style.getPropertyValue("--nx-header-h")).toBe("64px");
      expect(pRoot.style.getPropertyValue("--nx-card-radius")).toBe("16px");
    });
  }
});
