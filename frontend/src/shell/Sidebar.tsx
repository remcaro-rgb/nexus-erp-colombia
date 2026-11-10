// Sidebar (owner P08, UI-CONTRACT §5): "MÓDULOS" + collapse toggle; items grouped in registry order
// (Inicio · Finanzas · Comercial · Operaciones · Personas · Análisis · Sistema). Collapsed (80 px): icons only, group
// headings become separators, tooltips + aria-labels, footer hidden. Footer: system status from /api/health/ready.
// Modules the user may not access are ABSENT (server-filtered). Below 1024: 80 px rail (expanded = overlay drawer);
// below 768: off-canvas drawer.
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { NavLink } from "react-router";
import { cn } from "@lib/cn";
import { useT } from "@i18n/index";
import { Tooltip } from "@ds/components/Tooltip";
import { iconFor } from "./icons";
import { useShell } from "./ShellContext";

export function SidebarNav({ collapsed, onToggle, onNavigate, showToggle = true }: { collapsed: boolean; onToggle?: () => void; onNavigate?: () => void; showToggle?: boolean }) {
  const t = useT();
  const shell = useShell();
  const groups = shell.registry.groups.filter((g) => shell.registry.modules.some((m) => m.group === g));
  const status = shell.health === "ok" ? t("sidebar.status.ok") : shell.health === "degraded" ? t("sidebar.status.degraded") : t("sidebar.status.unknown");
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-12 shrink-0 items-center px-4", collapsed ? "justify-center" : "justify-between")}>
        {!collapsed ? <span className="text-xs font-semibold uppercase tracking-wider text-muted">{t("sidebar.title")}</span> : null}
        {showToggle && onToggle ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-controls="nx-sidebar-nav"
            aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            title={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            data-nx-control="sidebar-toggle"
            className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover"
          >
            {collapsed ? <PanelLeftOpen aria-hidden="true" size={18} /> : <PanelLeftClose aria-hidden="true" size={18} />}
          </button>
        ) : null}
      </div>
      <nav id="nx-sidebar-nav" aria-label={t("sidebar.nav")} className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {groups.map((g, gi) => {
          const items = shell.registry.modules.filter((m) => m.group === g);
          const showHeading = g !== items[0]?.label; // "Inicio" group holds only Inicio
          return (
            <div key={g} role="group" aria-label={g} className="mb-2">
              {collapsed ? (
                gi > 0 ? <hr aria-hidden="true" className="mx-2 my-2 border-line" /> : null
              ) : showHeading ? (
                <p aria-hidden="true" className="px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wider text-muted">
                  {g}
                </p>
              ) : null}
              <ul className="flex flex-col gap-1">
                {items.map((m) => {
                  const Icon = iconFor(m.icon);
                  const link = (
                    <NavLink
                      to={m.route}
                      onClick={onNavigate}
                      aria-label={collapsed ? m.label : undefined}
                      data-nx-module={m.id}
                      className={({ isActive }) =>
                        cn(
                          "flex min-h-10 items-center gap-3 rounded-control px-3 py-2.5 text-sm font-medium",
                          collapsed && "justify-center px-0",
                          isActive ? "bg-nav-active text-nav-active-fg shadow-sm" : "text-secondary hover:bg-nav-hover hover:text-fg",
                        )
                      }
                    >
                      <Icon aria-hidden="true" size={20} className="shrink-0" />
                      {!collapsed ? <span className="truncate">{m.label}</span> : null}
                    </NavLink>
                  );
                  return <li key={m.id}>{collapsed ? <Tooltip content={m.label}>{link}</Tooltip> : link}</li>;
                })}
              </ul>
            </div>
          );
        })}
      </nav>
      {!collapsed ? (
        <div className="shrink-0 border-t border-line px-4 py-3" data-nx-region="system-status">
          <p className="text-xs font-semibold text-muted">{t("sidebar.status")}</p>
          <p role="status" className={cn("text-xs font-semibold", shell.health === "ok" ? "text-positive" : shell.health === "degraded" ? "text-warning" : "text-muted")}>
            <span aria-hidden="true">● </span>
            {status}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside
      id="nx-sidebar"
      data-nx-region="sidebar"
      data-collapsed={collapsed}
      className={cn(
        "relative z-[20] h-full shrink-0 border-r border-line bg-surface transition-[width] duration-[var(--nx-motion-sidebar)]",
        collapsed ? "w-[var(--nx-sidebar-collapsed-w)]" : "w-[var(--nx-sidebar-w)]",
      )}
    >
      <SidebarNav collapsed={collapsed} onToggle={onToggle} />
    </aside>
  );
}
