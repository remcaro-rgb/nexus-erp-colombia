// The ONE internal shell (owner P08; module-registry.json › shell.rule): DEV banner · header 64 · sidebar 256/80 ·
// module bar 48 · center (only region that scrolls) · AI panel 320 (docked ≥ 1280, overlay below).
// Landmarks: banner (header), navigation (sidebar), main, complementary (AI). Skip link to main content.
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router";
import { useT } from "@i18n/index";
import { cn } from "@lib/cn";
import { Dialog } from "@ds/components/Dialog";
import { TokenScope } from "@ds/theme/ThemeProvider";
import { AiPanel } from "./AiPanel";
import { CommandPalette, rememberRecent, useCommandShortcut } from "./CommandPalette";
import { DevBanner } from "./DevBanner";
import { ErrorBoundary } from "./ErrorBoundary";
import { ExportProvider } from "./exportContext";
import { Header } from "./Header";
import { ModuleBar } from "./ModuleBar";
import { OfflineIndicator } from "./OfflineIndicator";
import { SessionTimeout } from "./SessionTimeout";
import { Sidebar, SidebarNav } from "./Sidebar";
import { useShell } from "./ShellContext";
import { useBreakpoint } from "./useBreakpoint";
import type { ResolvedModule } from "./registry";

export function SkipLink({ target = "nx-main" }: { target?: string }) {
  const t = useT();
  return (
    <a href={`#${target}`} className="nx-sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[60] focus:rounded-control focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-brand focus:shadow-lg">
      {t("app.skipToContent")}
    </a>
  );
}

export function AppShell({ module, children }: { module: ResolvedModule | null; children: ReactNode }) {
  const t = useT();
  const shell = useShell();
  const bp = useBreakpoint();
  const loc = useLocation();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openPalette = useCallback(() => setPaletteOpen(true), []);
  useCommandShortcut(openPalette);

  useEffect(() => setDrawerOpen(false), [loc.pathname]);
  useEffect(() => {
    if (module) rememberRecent(shell.userId, { label: module.label, route: loc.pathname });
  }, [loc.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  // lg: collapsed by default (user may expand). md: 80 px rail; expanding opens an overlay drawer. sm: off-canvas.
  const collapsed = bp === "md" ? true : shell.sidebarCollapsed;
  const toggleSidebar = () => (bp === "md" ? setDrawerOpen(true) : shell.setSidebarCollapsed(!shell.sidebarCollapsed));
  const idle = shell.context.session_idle_minutes ?? 30;

  return (
    <TokenScope variant="internal" className="flex h-screen flex-col overflow-hidden bg-app font-sans text-body">
      <SkipLink />
      {shell.context.environment === "dev" ? <DevBanner /> : null}
      <OfflineIndicator />
      <Header bp={bp} onOpenSearch={openPalette} onOpenNav={() => setDrawerOpen(true)} navOpen={drawerOpen} />
      <div className="flex min-h-0 flex-1">
        {bp !== "sm" ? <Sidebar collapsed={collapsed} onToggle={toggleSidebar} /> : null}
        <div className="flex min-w-0 flex-1 flex-col" data-nx-region="center-column">
          <ModuleBar module={module} />
          <main id="nx-main" tabIndex={-1} data-nx-region="main" className={cn("min-h-0 flex-1 overflow-y-auto bg-app p-4 md:p-6")}>
            <ErrorBoundary resetKey={loc.pathname}>{children}</ErrorBoundary>
          </main>
        </div>
        <AiPanel bp={bp} moduleId={module?.id ?? null} />
      </div>
      {bp === "sm" || bp === "md" ? (
        <Dialog open={drawerOpen} onOpenChange={setDrawerOpen} title={t("sidebar.title")} side="left" widthClass="w-[var(--nx-sidebar-w)]" hideTitle>
          <div className="-mx-5 -my-4 h-full">
            <SidebarNav collapsed={false} onNavigate={() => setDrawerOpen(false)} showToggle={false} />
          </div>
        </Dialog>
      ) : null}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <SessionTimeout idleMinutes={idle} onExpire={shell.logout} />
    </TokenScope>
  );
}
