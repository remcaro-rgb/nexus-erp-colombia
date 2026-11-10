// Portal shell variant (owner P08; F-PORTAL-CORE, UI-CONTRACT §19). Same tokens (<TokenScope>), light/dark theme,
// brand, DEV banner and top-bar geometry as the internal shell; top bar = brand, company name, notifications, theme,
// profile — NO company switcher, NO internal search, NO AI panel, NO admin. Left menu = portals[kind].menus only.
// External users never see internal routes; the portal API (/api/portal/v1/*) scopes everything by the token's party.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { NavLink, useParams } from "react-router";
import { Menu } from "lucide-react";
import { useT } from "@i18n/index";
import { cn } from "@lib/cn";
import { ApiError, type ApiClient } from "@lib/api/client";
import type { KpiResponse, KpiValue, NotificationItem, Page, PortalContext, PortalMe } from "@lib/api/types";
import { TokenScope } from "@ds/theme/ThemeProvider";
import { Dialog } from "@ds/components/Dialog";
import { EmptyState } from "@ds/components/Feedback";
import { KpiCard } from "@ds/components/KpiCard";
import { Brand } from "../Brand";
import { DevBanner } from "../DevBanner";
import { ErrorBoundary } from "../ErrorBoundary";
import { NotificationsView } from "../NotificationsPopover";
import { NotFound } from "../NotFound";
import { SessionTimeout } from "../SessionTimeout";
import { ThemeToggle } from "../ThemeToggle";
import { TopBar } from "../TopBar";
import { UserMenu } from "../UserMenu";
import { SkipLink } from "../AppShell";
import { useBreakpoint } from "../useBreakpoint";
import { discoverPortalDescriptors, portalById, type PortalDefinition, type PortalDescriptor, type PortalMenu } from "./registry";

interface PortalData {
  api: ApiClient;
  me: PortalMe;
  context: PortalContext;
  environment: string;
  brand: { name: string; logo_path: string | null };
  logout: () => void;
}

const PortalCtx = createContext<PortalData | null>(null);

export function usePortal(): PortalData {
  const v = useContext(PortalCtx);
  if (!v) throw new Error("usePortal must be used inside <PortalProvider>");
  return v;
}

export function PortalProvider({ api, children, onLogout, environment = "dev" }: { api: ApiClient; children: ReactNode; onLogout?: (url: string) => void; environment?: string }) {
  const t = useT();
  const [state, setState] = useState<{ me: PortalMe; context: PortalContext } | { error: unknown } | null>(null);
  useEffect(() => {
    const c = new AbortController();
    Promise.all([api.get<PortalMe>("/api/portal/v1/me", { signal: c.signal }), api.get<PortalContext>("/api/portal/v1/context", { signal: c.signal })])
      .then(([me, context]) => !c.signal.aborted && setState({ me, context }))
      .catch((error) => !c.signal.aborted && setState({ error }));
    return () => c.abort();
  }, [api]);
  if (!state) return <p className="p-6 text-sm text-muted">{t("app.loading")}</p>;
  if ("error" in state) return <p role="alert" className="p-6 text-sm text-danger">{t("app.contextError")}</p>;
  const logout = () => {
    const url = (state.context as PortalContext & { logout_url?: string }).logout_url ?? "/auth/logout";
    if (onLogout) onLogout(url);
    else window.location.assign(url);
  };
  return (
    <PortalCtx.Provider value={{ api, me: state.me, context: state.context, environment, brand: { name: "NexusERP", logo_path: null }, logout }}>
      {children}
    </PortalCtx.Provider>
  );
}

function PortalNotifications() {
  const p = usePortal();
  const [s, setS] = useState<{ items: NotificationItem[]; loading: boolean; error: boolean }>({ items: [], loading: true, error: false });
  useEffect(() => {
    const c = new AbortController();
    p.api
      .get<Page<NotificationItem>>("/api/portal/v1/notifications", { signal: c.signal })
      .then((r) => !c.signal.aborted && setS({ items: r.results ?? [], loading: false, error: false }))
      .catch(() => !c.signal.aborted && setS({ items: [], loading: false, error: true }));
    return () => c.abort();
  }, [p.api]);
  return <NotificationsView items={s.items} loading={s.loading} error={s.error} busy={false} onMarkAll={() => undefined} />;
}

function PortalNav({ def, onNavigate }: { def: PortalDefinition; onNavigate?: () => void }) {
  const t = useT();
  return (
    <nav aria-label={t("portal.nav")} className="min-h-0 flex-1 overflow-y-auto px-3 py-3" data-nx-portal-nav={def.kind}>
      <ul className="flex flex-col gap-1">
        {def.menus.map((m) => (
          <li key={m.id}>
            <NavLink
              to={m.route}
              end={m.route === def.route}
              onClick={onNavigate}
              data-nx-portal-menu={m.id}
              className={({ isActive }) =>
                cn("flex min-h-10 items-center rounded-control px-3 py-2.5 text-sm font-medium", isActive ? "bg-nav-active text-nav-active-fg shadow-sm" : "text-secondary hover:bg-nav-hover hover:text-fg")
              }
            >
              {m.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function PortalDashboard({ def }: { def: PortalDefinition }) {
  const p = usePortal();
  const t = useT();
  const [s, setS] = useState<{ data: KpiResponse | null; error: unknown; loading: boolean }>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    const c = new AbortController();
    setS({ data: null, error: null, loading: true });
    p.api
      .get<KpiResponse>(`/api/portal/v1/kpis`, { params: { portal: def.kind, compare: "previous" }, signal: c.signal })
      .then((data) => !c.signal.aborted && setS({ data, error: null, loading: false }))
      .catch((error) => !c.signal.aborted && setS({ data: null, error, loading: false }));
    return () => c.abort();
  }, [p.api, def.kind, nonce]);
  const unavailable = s.error instanceof ApiError && (s.error.status === 404 || s.error.status === 501);
  const byId = new Map<string, KpiValue>((s.data?.kpis ?? []).map((k) => [k.id, k]));
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-bold text-fg">{def.label}</h1>
      <section aria-label={t("dashboard.kpis")} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {def.dashboard.kpis.map((k) => (
          <KpiCard
            key={k.id}
            def={k}
            loading={s.loading}
            error={Boolean(s.error) && !unavailable}
            onRetry={() => setNonce((n) => n + 1)}
            value={unavailable || (s.data && !byId.has(k.id)) ? { id: k.id, value: null, previous: null, delta: null, unit: k.unit, state: "not_available" } : (byId.get(k.id) ?? null)}
            drillHref={k.drill ?? null}
          />
        ))}
      </section>
      {s.data && s.data.kpis.every((k) => k.state === "no_data") && def.dashboard.empty_state ? (
        <div className="nx-card">
          <EmptyState title={def.dashboard.empty_state} />
        </div>
      ) : null}
    </div>
  );
}

export function PortalShell({ def, children }: { def: PortalDefinition; children: ReactNode }) {
  const t = useT();
  const p = usePortal();
  const bp = useBreakpoint();
  const [drawer, setDrawer] = useState(false);
  const allowed = p.me.portal_kind === def.kind;
  const name = p.me.display_name ?? p.me.email ?? p.me.id;
  const left = (
    <>
      {bp === "sm" && allowed ? (
        <button type="button" aria-label={t("header.menu")} aria-expanded={drawer} onClick={() => setDrawer(true)} className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-control text-secondary hover:bg-nav-hover">
          <Menu aria-hidden="true" size={18} />
        </button>
      ) : null}
      <Brand name={p.brand.name} logoPath={p.brand.logo_path} to={def.route} />
      {p.context.company_name ? (
        <span className="truncate text-sm font-medium text-secondary" data-nx-portal-company="">
          {p.context.company_name}
        </span>
      ) : null}
    </>
  );
  const right = (
    <>
      {allowed ? <PortalNotifications /> : null}
      <ThemeToggle />
      <UserMenu name={name} onLogout={p.logout} />
    </>
  );
  return (
    <TokenScope variant="portal" className="flex h-screen flex-col overflow-hidden bg-app font-sans text-body">
      <SkipLink />
      {p.environment === "dev" ? <DevBanner /> : null}
      <TopBar left={left} right={right} />
      <div className="flex min-h-0 flex-1">
        {allowed && bp !== "sm" ? (
          <aside data-nx-region="sidebar" className="flex h-full w-[var(--nx-sidebar-w)] shrink-0 flex-col border-r border-line bg-surface">
            <p className="flex h-12 items-center px-4 text-xs font-semibold uppercase tracking-wider text-muted">{def.label}</p>
            <PortalNav def={def} />
          </aside>
        ) : null}
        <main id="nx-main" tabIndex={-1} data-nx-region="main" className="min-h-0 flex-1 overflow-y-auto bg-app p-4 md:p-6">
          {allowed ? (
            <ErrorBoundary>{children}</ErrorBoundary>
          ) : (
            <div role="alert" className="nx-card">
              <EmptyState title={t("portal.forbidden")} />
            </div>
          )}
        </main>
      </div>
      {allowed && bp === "sm" ? (
        <Dialog open={drawer} onOpenChange={setDrawer} title={def.label} side="left" widthClass="w-[var(--nx-sidebar-w)]">
          <PortalNav def={def} onNavigate={() => setDrawer(false)} />
        </Dialog>
      ) : null}
      <SessionTimeout idleMinutes={p.context.session_idle_minutes ?? 30} onExpire={p.logout} />
    </TokenScope>
  );
}

/** Route element for /portal/:portal/* */
export function PortalRoute({ descriptors }: { descriptors?: PortalDescriptor[] }) {
  const params = useParams();
  const def = portalById(params.portal);
  if (!def) return <NotFound home="/" />;
  const menuSeg = (params["*"] ?? "").split("/").filter(Boolean)[0];
  const menu: PortalMenu | undefined = menuSeg ? def.menus.find((m) => m.route === `${def.route}/${menuSeg}`) : def.menus.find((m) => m.route === def.route);
  const pages = (descriptors ?? discoverPortalDescriptors()).find((d) => d.portal === def.id)?.pages ?? {};
  const Page = menu ? pages[menu.id] : undefined;
  return (
    <PortalShell def={def}>
      {!menu ? (
        <NotFound home={def.route} />
      ) : Page ? (
        <Page portal={def} menu={menu} />
      ) : menu.route === def.route ? (
        <PortalDashboard def={def} />
      ) : (
        <PortalPending label={menu.label} />
      )}
    </PortalShell>
  );
}

function PortalPending({ label }: { label: string }) {
  const t = useT();
  return (
    <div className="nx-card">
      <EmptyState title={t("kpi.notAvailable")} body={label} />
    </div>
  );
}

