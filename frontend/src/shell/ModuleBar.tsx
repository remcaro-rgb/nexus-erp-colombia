// Module bar (owner P08, UI-CONTRACT §6): 48 px under the header. Left: submenu pills (links, aria-current="page"),
// horizontal scroll with a fade and a "Más" menu. Right: quick action (only with its permission, real route) and export.
import * as DM from "@radix-ui/react-dropdown-menu";
import { Download, MoreHorizontal, Plus } from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";
import { Tooltip } from "@ds/components/Tooltip";
import { useToast } from "@ds/components/Toast";
import { ApiError } from "@lib/api/client";
import { useShell } from "./ShellContext";
import { useExportSpec } from "./exportContext";
import type { ResolvedModule } from "./registry";

export function ModuleBar({ module }: { module: ResolvedModule | null }) {
  const t = useT();
  const shell = useShell();
  const nav = useNavigate();
  const toast = useToast();
  const spec = useExportSpec();
  const [exporting, setExporting] = useState(false);
  if (!module) return <div data-nx-region="module-bar" className="h-12 shrink-0 border-b border-line bg-surface" />;
  const quick = module.quick_action;
  const quickRoute = quick?.action.startsWith("route:") ? quick.action.slice(6) : null;

  async function doExport() {
    if (!spec) return;
    setExporting(true);
    try {
      await shell.api.post("/api/v1/dataio/exports", {
        body: { entity: spec.entity, params: spec.params ?? {}, company_id: shell.companyId, period_id: shell.period?.id ?? null },
        headers: shell.companyId ? { "X-Company-Id": shell.companyId } : undefined,
      });
      toast.push({ tone: "success", title: t("moduleBar.exportStarted") });
    } catch (e) {
      toast.push({ tone: "error", title: e instanceof Error ? e.message : String(e), traceId: e instanceof ApiError ? e.traceId : null });
    } finally {
      setExporting(false);
    }
  }

  const pill = ({ isActive }: { isActive: boolean }) =>
    cn(
      "inline-flex min-h-8 shrink-0 items-center rounded-[12px] border px-3 py-1.5 text-xs font-medium whitespace-nowrap",
      isActive ? "border-line bg-nav-hover font-semibold text-brand" : "border-transparent text-secondary hover:bg-nav-hover hover:text-fg",
    );

  return (
    <div data-nx-region="module-bar" className="relative z-[10] flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-4 md:px-6">
      <nav aria-label={t("moduleBar.nav", { module: module.label })} className="relative min-w-0 flex-1">
        <ul className="flex items-center gap-1 overflow-x-auto [scrollbar-width:none] pr-8 [mask-image:linear-gradient(to_right,black_85%,transparent)]">
          {module.submenus.map((s) => (
            <li key={s.id}>
              <NavLink to={s.route} end={s.route === module.route} className={pill} data-nx-submenu={s.id}>
                {s.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      {module.submenus.length > 4 ? (
        <DM.Root>
          <DM.Trigger asChild>
            <button type="button" className="inline-flex min-h-8 items-center gap-1 rounded-[12px] px-2 text-xs font-medium text-secondary hover:bg-nav-hover">
              <MoreHorizontal aria-hidden="true" size={14} />
              {t("moduleBar.more")}
            </button>
          </DM.Trigger>
          <DM.Portal>
            <DM.Content align="end" sideOffset={6} className="z-[50] min-w-48 rounded-card border border-line bg-surface p-1 shadow-xl">
              {module.submenus.map((s) => (
                <DM.Item key={s.id} onSelect={() => nav(s.route)} className="flex min-h-8 cursor-pointer items-center rounded-control px-3 text-sm text-body outline-none data-[highlighted]:bg-nav-hover">
                  {s.label}
                </DM.Item>
              ))}
            </DM.Content>
          </DM.Portal>
        </DM.Root>
      ) : null}
      <div className="flex shrink-0 items-center gap-2">
        {quick && quickRoute ? (
          <Link
            to={quickRoute}
            data-nx-control="quick-action"
            className="inline-flex min-h-8 items-center gap-1.5 rounded-control bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover dark:text-slate-950"
          >
            <Plus aria-hidden="true" size={14} />
            {quick.label}
          </Link>
        ) : null}
        {spec ? (
          <button
            type="button"
            aria-label={t("moduleBar.export")}
            title={t("moduleBar.export")}
            disabled={exporting}
            aria-busy={exporting || undefined}
            onClick={() => void doExport()}
            data-nx-control="export"
            className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control border border-line text-secondary hover:bg-nav-hover"
          >
            <Download aria-hidden="true" size={16} />
          </button>
        ) : (
          <Tooltip content={t("moduleBar.exportUnavailable")} side="bottom">
            <button
              type="button"
              aria-label={`${t("moduleBar.export")}: ${t("moduleBar.exportUnavailable")}`}
              aria-disabled="true"
              data-nx-control="export"
              onClick={(e) => e.preventDefault()}
              className="inline-flex min-h-8 min-w-8 cursor-not-allowed items-center justify-center rounded-control border border-line text-muted opacity-60"
            >
              <Download aria-hidden="true" size={16} />
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
