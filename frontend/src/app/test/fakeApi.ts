// TEST-ONLY fake backend (owner P08). Synthetic data (no real companies or people); simulates the server-side
// filtering contract of /api/v1/ui/modules (unauthorized modules absent). Never imported by application code.
import registry from "@spec/ui/module-registry.json";
import type { RegistryModule, UiContext, UiModules } from "@lib/api/types";
import type { Fetcher } from "@lib/api/client";

export const ALL_MODULES = (registry as unknown as { modules: RegistryModule[] }).modules;
export const GROUPS = (registry as unknown as { groups: string[] }).groups;

export function allPermissions(): string[] {
  const s = new Set<string>();
  for (const m of ALL_MODULES) {
    s.add(m.permission);
    for (const sub of m.submenus) {
      s.add(sub.permission);
      for (const t of sub.tabs ?? []) s.add(t.permission);
    }
    if (m.quick_action) s.add(m.quick_action.permission);
    for (const k of m.dashboard?.kpis ?? []) s.add(k.permission);
  }
  return [...s];
}

export interface FakeOptions {
  permissions?: string[];
  companies?: Array<{ id: string; legal_name: string; nit?: string }>;
  portalKind?: "supplier" | "customer" | "employee" | null;
  routes?: Record<string, (url: URL, init?: RequestInit) => unknown | Promise<unknown>>;
  delayMs?: number;
}

export interface FakeApi {
  fetcher: Fetcher;
  calls: Array<{ method: string; url: string }>;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export function fakeApi(opts: FakeOptions = {}): FakeApi {
  const perms = opts.permissions ?? allPermissions();
  const companies = opts.companies ?? [
    { id: "c-1", legal_name: "Demo Andina S.A.S.", nit: "900000001-1" },
    { id: "c-2", legal_name: "Demo Caribe S.A.S.", nit: "900000002-1" },
  ];
  const calls: FakeApi["calls"] = [];
  const fetcher: Fetcher = async (input, init) => {
    const url = new URL(input, "http://127.0.0.1");
    const method = init?.method ?? "GET";
    calls.push({ method, url: url.pathname + url.search });
    if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs));
    if (init?.signal?.aborted) throw new DOMException("aborted", "AbortError");
    const custom = opts.routes?.[url.pathname];
    if (custom) {
      const out = await custom(url, init);
      return out instanceof Response ? out : json(out);
    }
    const companyId = url.searchParams.get("company_id") ?? companies[0]?.id ?? null;
    switch (url.pathname) {
      case "/api/v1/ui/context": {
        const ctx: UiContext = {
          tenant_id: "t-1",
          company_id: companyId,
          companies,
          period: { id: `p-${companyId}-2026-11`, start: "2026-11-01", end: "2026-11-30", status: "open" },
          periods: [
            { id: `p-${companyId}-2026-11`, start: "2026-11-01", end: "2026-11-30", status: "open" },
            { id: `p-${companyId}-2026-10`, start: "2026-10-01", end: "2026-10-31", status: "closed" },
          ],
          roles: ["admin"],
          permissions: perms,
          environment: "dev",
          brand: { name: "NexusERP", logo_path: null },
          user: { display_name: "Usuario Sintético" },
        };
        return json(ctx);
      }
      case "/api/v1/iam/me":
        return json({ id: "u-1", user_id: "u-1", tenant_id: "t-1", companies: companies.map((c) => c.id), permissions: perms, field_masks: {} });
      case "/api/v1/ui/modules": {
        const body: UiModules = { groups: GROUPS, modules: ALL_MODULES.filter((m) => perms.includes(m.permission)) };
        return json(body);
      }
      case "/api/health/ready":
        return json({ status: "ok" });
      case "/api/v1/workflow/notifications":
        return json({ results: [] });
      case "/api/portal/v1/me":
        return json({ id: "pa-1", portal_kind: opts.portalKind ?? "supplier", display_name: "Cuenta Externa Sintética" });
      case "/api/portal/v1/context":
        return json({ id: "pc-1", company_name: "Demo Andina S.A.S.", session_idle_minutes: 30 });
      default:
        return json({ type: "about:blank", title: "Not Found", status: 404, code: "not_found", trace_id: "trace-404" }, 404);
    }
  };
  return { fetcher, calls };
}
