// Module routes (owner P08): /m/{module} = dashboard, /m/{module}/{submenu}[/{tab}] = stable deep links.
// Pages come from the module's descriptor (features/<x>/module.ts). A submenu whose owner has not delivered its page yet
// shows the honest state "Módulo aún no disponible" (no fake screens). Tabs = ARIA tablist, permission-checked (absent when
// not allowed), deep-linkable.
import { Suspense } from "react";
import { useNavigate, useParams } from "react-router";
import { useT } from "@i18n/index";
import { Tabs } from "@ds/components/Tabs";
import { EmptyState, Skeleton } from "@ds/components/Feedback";
import { ModuleDashboard } from "./ModuleDashboard";
import { NotFound } from "./NotFound";
import { matchModuleRoute, type LazyPage } from "./registry";
import { useShell } from "./ShellContext";

export function useCurrentModule() {
  const params = useParams();
  const shell = useShell();
  const rest = (params["*"] ?? "").split("/").filter(Boolean);
  return matchModuleRoute(shell.registry, params.module ?? "", rest[0], rest[1]);
}

export function ModulePage() {
  const t = useT();
  const nav = useNavigate();
  const params = useParams();
  const { module, submenu, tab } = useCurrentModule();
  const rest = (params["*"] ?? "").split("/").filter(Boolean);
  if (!module) return <NotFound />;
  if (rest.length > 0 && !submenu) return <NotFound />;
  const isDashboard = !submenu || submenu.route === module.route;
  const pageKey = submenu ? (tab ? `${submenu.id}/${tab.id}` : submenu.id) : "resumen";
  const Page: LazyPage | undefined = module.pages[pageKey] ?? (submenu ? module.pages[submenu.id] : undefined) ?? (isDashboard ? module.pages.resumen : undefined);
  const content = Page ? (
    <Suspense fallback={<Skeleton className="h-48 w-full" label={t("app.loading")} />}>
      <Page module={module} submenu={submenu} tab={tab} />
    </Suspense>
  ) : isDashboard ? (
    <ModuleDashboard module={module} />
  ) : (
    <div className="nx-card">
      <EmptyState title={t("kpi.notAvailable")} body={submenu?.label} />
    </div>
  );
  if (submenu?.tabs?.length) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-bold text-fg">{submenu.label}</h1>
        <Tabs tabs={submenu.tabs.map((x) => ({ id: x.id, label: x.label }))} selected={tab?.id ?? submenu.tabs[0]?.id ?? ""} onSelect={(id) => nav(submenu.tabs?.find((x) => x.id === id)?.route ?? submenu.route)} label={submenu.label} idPrefix={`nx-${module.id}-${submenu.id}`}>
          {content}
        </Tabs>
      </div>
    );
  }
  return content;
}
