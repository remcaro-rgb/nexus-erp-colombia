// Module dashboard (owner P08 pattern, UI-CONTRACT §7): KPI row (grid 1/2/4, gap 16) + chart panel (2fr) + feed (1fr).
// Values only from GET /api/v1/kpis/{module} and …/panels/{panel}; a provider endpoint that does not exist yet
// (404/501) renders "Módulo aún no disponible" — never static, sample or random numbers.
import { useT } from "@i18n/index";
import { ApiError } from "@lib/api/client";
import type { KpiResponse, KpiValue, PanelResponse, RegistryPanel } from "@lib/api/types";
import { can } from "@lib/permissions";
import { KpiCard } from "@ds/components/KpiCard";
import { ChartPanel } from "@ds/components/ChartPanel";
import { FeedPanel } from "@ds/components/FeedPanel";
import { useResource, useShell } from "./ShellContext";
import type { ResolvedModule } from "./registry";
import { HomeWidgets } from "./widgets";

function notProvided(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 501);
}

function drill(href: string | undefined, periodId: string | null | undefined): string | null {
  if (!href) return null;
  return href.replace("{period}", encodeURIComponent(periodId ?? "")).replace(/[?&][^=]+=\{[^}]+\}/g, "");
}

function Panel({ module, panel }: { module: ResolvedModule; panel: RegistryPanel }) {
  const shell = useShell();
  const res = useResource<PanelResponse>(`/api/v1/kpis/${module.id}/panels/${panel.id}`, { company_id: shell.companyId, period_id: shell.period?.id ?? null });
  const data = res.error ? null : res.data;
  if (panel.type === "feed" || panel.type === "list") {
    return <FeedPanel title={panel.title} items={data?.items ?? null} loading={res.loading} />;
  }
  return <ChartPanel title={panel.title} data={data} loading={res.loading} />;
}

export function ModuleDashboard({ module }: { module: ResolvedModule }) {
  const t = useT();
  const shell = useShell();
  const kpiDefs = (module.dashboard?.kpis ?? []).filter((k) => can(shell.perms, k.permission));
  const panels = (module.dashboard?.panels ?? []).filter((p) => !p.permission || can(shell.perms, p.permission));
  const res = useResource<KpiResponse>(kpiDefs.length ? `/api/v1/kpis/${module.id}` : null, {
    company_id: shell.companyId,
    period_id: shell.period?.id ?? null,
    compare: "previous",
  });
  const byId = new Map<string, KpiValue>((res.data?.kpis ?? []).map((k) => [k.id, k]));
  const unavailable = notProvided(res.error);
  const failed = Boolean(res.error) && !unavailable;
  const chart = panels.filter((p) => p.type !== "feed" && p.type !== "list");
  const feed = panels.filter((p) => p.type === "feed" || p.type === "list");
  const widgets = module.descriptor?.dashboardWidgets?.filter((w) => can(shell.perms, w.permission)) ?? [];
  return (
    <div className="flex flex-col gap-6" data-nx-dashboard={module.id}>
      <h1 className="nx-sr-only">{t("dashboard.title", { module: module.label })}</h1>
      {kpiDefs.length ? (
        <section aria-label={t("dashboard.kpis")} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {kpiDefs.map((k) => (
            <KpiCard
              key={k.id}
              def={k}
              loading={res.loading}
              error={failed}
              onRetry={res.reload}
              value={unavailable ? { id: k.id, value: null, previous: null, delta: null, unit: k.unit, state: "not_available" } : (byId.get(k.id) ?? (res.data ? { id: k.id, value: null, previous: null, delta: null, unit: k.unit, state: "not_available" } : null))}
              drillHref={drill(k.drill, shell.period?.id)}
            />
          ))}
        </section>
      ) : null}
      {chart.length || feed.length ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
          <div className="flex flex-col gap-6">
            {chart.map((p) => (
              <Panel key={p.id} module={module} panel={p} />
            ))}
          </div>
          <div className="flex flex-col gap-6">
            {feed.map((p) => (
              <Panel key={p.id} module={module} panel={p} />
            ))}
          </div>
        </div>
      ) : null}
      {widgets.map((w) => {
        const W = w.component;
        return <W key={w.id} module={module} submenu={null} tab={null} />;
      })}
      {module.id === "inicio" ? <HomeWidgets /> : null}
    </div>
  );
}
