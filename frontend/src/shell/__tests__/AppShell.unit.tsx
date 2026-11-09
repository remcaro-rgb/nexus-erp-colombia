// Shell unit tests (P08): T-P08-05 route registry hides modules without permission; layout regions and toggles;
// company switch invalidates the cache without a stale render (UI-CONTRACT §11); Ctrl/Cmd+K palette.
import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { buildRoutes } from "@app/App";
import { allPermissions, fakeApi, ALL_MODULES, GROUPS, type FakeOptions } from "@app/test/fakeApi";
import { createApiClient } from "@lib/api/client";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";

function renderAt(path: string, opts: FakeOptions = {}) {
  const api = fakeApi(opts);
  const router = createMemoryRouter(buildRoutes(createApiClient(api.fetcher)), { initialEntries: [path] });
  const utils = render(
    <DesignSystemProvider theme="light">
      <RouterProvider router={router} />
    </DesignSystemProvider>,
  );
  return { ...utils, api, router };
}

const sidebarLinks = () => within(screen.getByRole("navigation", { name: "Navegación de módulos" })).getAllByRole("link");

describe("P08 internal shell", () => {
  window.innerWidth = 1440;

  it("T-P08-05 shows all 15 registry modules in group order for a full-access user", async () => {
    renderAt("/m/inicio");
    await screen.findByRole("navigation", { name: "Navegación de módulos" });
    const ids = sidebarLinks().map((a) => a.getAttribute("data-nx-module"));
    expect(ids).toHaveLength(15);
    const order = ALL_MODULES.slice().sort((a, b) => GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group)).map((m) => m.id);
    expect(ids).toEqual(order);
    expect(within(screen.getByRole("navigation", { name: "Navegación de módulos" })).getByRole("link", { name: "Inicio" })).toHaveAttribute("aria-current", "page");
  });

  it("T-P08-05 hides modules, submenus and quick actions without permission (absent, not disabled)", async () => {
    const perms = allPermissions().filter((p) => p !== "hr.access" && p !== "ledger.close" && p !== "ledger.post");
    renderAt("/m/contabilidad", { permissions: perms });
    await screen.findByRole("navigation", { name: "Navegación de módulos" });
    expect(sidebarLinks().map((a) => a.getAttribute("data-nx-module"))).not.toContain("talento");
    expect(screen.queryByText("Talento humano y nómina")).toBeNull();
    const bar = screen.getByRole("navigation", { name: "Secciones de Contabilidad" });
    expect(within(bar).queryByRole("link", { name: "Cierres de periodo" })).toBeNull();
    expect(within(bar).getByRole("link", { name: "Libro mayor" })).toBeInTheDocument();
    expect(document.querySelector('[data-nx-control="quick-action"]')).toBeNull();
    // A direct URL to a hidden module renders "not found", never the module.
    renderAt("/m/talento", { permissions: perms });
    expect(await screen.findAllByText("Página no encontrada")).not.toHaveLength(0);
  });

  it("T-P08-05 layout regions, toggles with aria-expanded and per-user persistence", async () => {
    const user = userEvent.setup();
    renderAt("/m/contabilidad");
    await screen.findByRole("navigation", { name: "Navegación de módulos" });
    for (const r of ["dev-banner", "header", "sidebar", "module-bar", "main"]) expect(document.querySelector(`[data-nx-region="${r}"]`)).not.toBeNull();
    const quick = document.querySelector('[data-nx-control="quick-action"]');
    expect(quick).toHaveAttribute("href", "/m/contabilidad/comprobantes/nuevo");
    expect(screen.getByRole("link", { name: "Resumen" })).toHaveAttribute("aria-current", "page");

    const toggle = screen.getByRole("button", { name: "Contraer barra lateral" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Expandir barra lateral" })).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector('[data-nx-region="sidebar"]')).toHaveAttribute("data-collapsed", "true");
    expect(window.localStorage.getItem("nexus:u-1:sidebar.collapsed")).toBe("true");
    expect(document.querySelector('[data-nx-region="system-status"]')).toBeNull(); // footer hidden when collapsed

    const ai = screen.getByRole("button", { name: "Asistente IA" });
    expect(ai).toHaveAttribute("aria-expanded", "false");
    expect(ai).toHaveAttribute("aria-controls", "nx-ai-panel");
    await user.click(ai);
    expect(ai).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById("nx-ai-panel");
    expect(panel).toHaveAttribute("data-nx-region", "ai-panel"); // docked aside at ≥ 1280
    expect(panel?.tagName).toBe("ASIDE");
    expect(within(panel as HTMLElement).getByText("El asistente aún no está disponible en este entorno.")).toBeInTheDocument();
    await user.click(within(panel as HTMLElement).getByRole("button", { name: "Cerrar asistente" }));
    expect(document.getElementById("nx-ai-panel")).toBeNull();
  });

  it("T-P08-05 dashboards render honest not_available states (no invented numbers)", async () => {
    renderAt("/m/inicio");
    await waitFor(() => expect(document.querySelectorAll("[data-kpi]").length).toBeGreaterThanOrEqual(3));
    await waitFor(() => expect(document.querySelector('[data-kpi][data-state="loading"]')).toBeNull());
    for (const c of document.querySelectorAll("[data-kpi]")) expect(c.getAttribute("data-state")).toBe("not_available");
    expect(document.querySelector('[data-nx-region="main"]')?.textContent).not.toMatch(/\$\s?\d/);
  });

  it("T-P08-05 company switch clears the cache and never shows the previous company's KPI", async () => {
    const pending: Array<() => void> = [];
    const kpis = (url: URL) => {
      const company = url.searchParams.get("company_id");
      const body = {
        as_of: "2026-11-15",
        generated_at: "2026-11-15T10:00:00-05:00",
        kpis: [{ id: "ventas_netas", value: company === "c-1" ? "1284500" : "777000", previous: null, delta: "4.2", unit: "COP", state: "ok" }],
      };
      if (company === "c-1") return body;
      return new Promise<Response>((resolve) => {
        pending.push(() => resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })));
      });
    };
    const { api } = renderAt("/m/inicio", { routes: { "/api/v1/kpis/inicio": kpis } });
    expect(await screen.findByText("$ 1.284.500")).toBeInTheDocument();
    expect(screen.getByText("+4,2 %")).toBeInTheDocument();

    const select = document.querySelector('[data-nx-control="company"]') as HTMLSelectElement;
    act(() => {
      fireEvent.change(select, { target: { value: "c-2" } });
    });
    // While company 2 is pending, company 1's value must be gone.
    await waitFor(() => expect(screen.queryByText("$ 1.284.500")).toBeNull());
    await waitFor(() => expect(pending.length).toBeGreaterThan(0));
    act(() => pending.splice(0).forEach((r) => r()));
    expect(await screen.findByText("$ 777.000")).toBeInTheDocument();
    expect(screen.queryByText("$ 1.284.500")).toBeNull();
    const kpiCalls = api.calls.filter((c) => c.url.startsWith("/api/v1/kpis/inicio?"));
    const c2 = kpiCalls.filter((c) => c.url.includes("company_id=c-2"));
    expect(c2).toHaveLength(1); // no request with a half-switched context
    expect(c2[0]?.url).toContain("period_id=p-c-2-2026-11");
    expect(api.calls.some((c) => c.url.startsWith("/api/v1/ui/modules?company_id=c-2"))).toBe(true);
  });

  it("T-P08-05 Ctrl/Cmd+K opens the command palette with navigation and Esc closes it", async () => {
    const user = userEvent.setup();
    const { router } = renderAt("/m/inicio");
    await screen.findByRole("navigation", { name: "Navegación de módulos" });
    await user.keyboard("{Control>}k{/Control}");
    const box = await screen.findByRole("combobox", { name: /Escriba para buscar/ });
    expect(box).toHaveFocus();
    await user.type(box, "Tesor");
    const opt = await screen.findByRole("option", { name: "Tesorería" });
    expect(opt).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("option", { name: "Preguntar a la IA: “Tesor”" })).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/m/tesoreria"));
    await user.keyboard("{Meta>}k{/Meta}");
    await screen.findByRole("combobox", { name: /Escriba para buscar/ });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("combobox", { name: /Escriba para buscar/ })).toBeNull());
  });

  it("T-P08-05 submenu tabs are an ARIA tablist with deep links", async () => {
    const user = userEvent.setup();
    const { router } = renderAt("/m/contabilidad/dimensiones");
    const tablist = await screen.findByRole("tablist", { name: "Dimensiones contables" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Centros de costo", "Unidades de negocio", "Auxiliares abiertos", "Ítems contables"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    tabs[0]?.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/m/contabilidad/dimensiones/unidades-negocio"));
    await user.keyboard("{End}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/m/contabilidad/dimensiones/items-contables"));
  });
});
