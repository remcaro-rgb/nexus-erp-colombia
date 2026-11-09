// Shell data layer (owner P08): /api/v1/ui/context + /api/v1/iam/me + /api/v1/ui/modules, company/period switching
// with cache invalidation (UI-CONTRACT §11), per-user layout preferences and the system-status source (/api/health/ready).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ApiClient } from "@lib/api/client";
import type { Me, PeriodRef, UiContext, UiModules } from "@lib/api/types";
import { ContextStore, StaleContextError } from "@lib/context/store";
import { permissionHash, toPermissionSet, type FieldMasks, type PermissionSet } from "@lib/permissions";
import { prefKey, readPref, writePref } from "@lib/storage";
import { resolveRegistry, type ModuleDescriptor, type ResolvedRegistry } from "./registry";

export type HealthState = "ok" | "degraded" | "unknown";

export interface ShellData {
  api: ApiClient;
  store: ContextStore;
  context: UiContext;
  me: Me;
  perms: PermissionSet;
  masks: FieldMasks;
  registry: ResolvedRegistry;
  companyId: string | null;
  period: PeriodRef | null;
  setCompany: (id: string) => void;
  setPeriod: (p: PeriodRef) => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (v: boolean) => void;
  aiOpen: boolean;
  setAiOpen: (v: boolean) => void;
  health: HealthState;
  logout: () => void;
  userId: string;
  /** True while a company/period switch is loading its new context: data hooks wait instead of fetching. */
  switching: boolean;
}

const ShellCtx = createContext<ShellData | null>(null);

export function useShell(): ShellData {
  const v = useContext(ShellCtx);
  if (!v) throw new Error("useShell must be used inside <ShellProvider>");
  return v;
}

export function useOptionalShell(): ShellData | null {
  return useContext(ShellCtx);
}

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; error: unknown }
  | { phase: "ready"; context: UiContext; me: Me; modules: UiModules; companyId: string | null; period: PeriodRef | null; switching?: boolean };

export interface ShellProviderProps {
  api: ApiClient;
  children: ReactNode;
  descriptors?: ModuleDescriptor[];
  renderLoading?: () => ReactNode;
  renderError?: (error: unknown, retry: () => void) => ReactNode;
  onLogout?: (logoutUrl: string) => void;
}

export function ShellProvider({ api, children, descriptors, renderLoading, renderError, onLogout }: ShellProviderProps) {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  const storeRef = useRef<ContextStore>(new ContextStore({ tenantId: "", companyId: null, periodId: null, permissionHash: "" }));
  const [health, setHealth] = useState<HealthState>("unknown");

  const load = useCallback(
    async (companyId: string | null, period: PeriodRef | null, signal: AbortSignal) => {
      const [context, me] = await Promise.all([
        api.get<UiContext>("/api/v1/ui/context", { params: { company_id: companyId }, signal }),
        api.get<Me>("/api/v1/iam/me", { signal, headers: companyId ? { "X-Company-Id": companyId } : undefined }),
      ]);
      const cid = context.company_id ?? companyId;
      const modules = await api.get<UiModules>("/api/v1/ui/modules", { params: { company_id: cid }, signal });
      const p = period && context.period && period.id !== context.period.id ? period : context.period;
      return { context, me, modules, companyId: cid, period: p };
    },
    [api],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ phase: "loading" });
    load(null, null, ctrl.signal)
      .then((r) => {
        if (ctrl.signal.aborted) return;
        storeRef.current.update({
          tenantId: r.context.tenant_id,
          companyId: r.companyId,
          periodId: r.period?.id ?? null,
          permissionHash: permissionHash(toPermissionSet(r.me.permissions ?? r.context.permissions)),
        });
        setState({ phase: "ready", ...r });
      })
      .catch((error) => {
        if (!ctrl.signal.aborted) setState({ phase: "error", error });
      });
    return () => ctrl.abort();
  }, [load, attempt]);

  useEffect(() => {
    let alive = true;
    const probe = () =>
      api
        .get<{ status?: string }>("/api/health/ready")
        .then((r) => alive && setHealth(r?.status && r.status !== "ok" ? "degraded" : "ok"))
        .catch(() => alive && setHealth("degraded"));
    void probe();
    const h = window.setInterval(probe, 60_000);
    return () => {
      alive = false;
      window.clearInterval(h);
    };
  }, [api]);

  const ready = state.phase === "ready" ? state : null;
  const userId = ready?.me.user_id ?? ready?.me.id ?? "anon";
  const [sidebarCollapsed, setCollapsed] = useState(false);
  const [aiOpen, setAi] = useState(false);
  useEffect(() => {
    if (!ready) return;
    setCollapsed(readPref(prefKey(userId, "sidebar.collapsed"), false));
    setAi(readPref(prefKey(userId, "ai.open"), false));
  }, [ready?.me.id, userId]); // eslint-disable-line react-hooks/exhaustive-deps

  const switchContext = useCallback(
    (companyId: string | null, period: PeriodRef | null) => {
      // Abort + clear BEFORE the new request so the previous company's data never renders while pending.
      storeRef.current.update({ companyId, periodId: period?.id ?? null });
      setState((s) => (s.phase === "ready" ? { ...s, companyId, period, switching: true } : s));
      const signal = storeRef.current.signal;
      load(companyId, period, signal)
        .then((r) => {
          if (signal.aborted) return;
          const perms = toPermissionSet(r.me.permissions ?? r.context.permissions);
          storeRef.current.update({ companyId: r.companyId, periodId: r.period?.id ?? null, permissionHash: permissionHash(perms) });
          setState({ phase: "ready", ...r });
        })
        .catch((error) => {
          if (!(error instanceof StaleContextError) && !signal.aborted) setState({ phase: "error", error });
        });
    },
    [load],
  );

  const value = useMemo<ShellData | null>(() => {
    if (!ready) return null;
    const perms = toPermissionSet(ready.me.permissions ?? ready.context.permissions);
    return {
      api,
      store: storeRef.current,
      context: ready.context,
      me: ready.me,
      perms,
      masks: ready.me.field_masks ?? {},
      registry: resolveRegistry(ready.modules, perms, descriptors),
      companyId: ready.companyId,
      period: ready.period,
      setCompany: (id) => switchContext(id, null),
      setPeriod: (p) => switchContext(ready.companyId, p),
      sidebarCollapsed,
      setSidebarCollapsed: (v) => {
        setCollapsed(v);
        writePref(prefKey(userId, "sidebar.collapsed"), v);
      },
      aiOpen,
      setAiOpen: (v) => {
        setAi(v);
        writePref(prefKey(userId, "ai.open"), v);
      },
      health,
      userId,
      switching: Boolean(ready.switching),
      logout: () => {
        storeRef.current.clear();
        const url = (ready.context as UiContext & { logout_url?: string }).logout_url ?? "/auth/logout";
        if (onLogout) onLogout(url);
        else window.location.assign(url);
      },
    };
  }, [ready, api, descriptors, switchContext, sidebarCollapsed, aiOpen, health, userId, onLogout]);

  if (state.phase === "loading") return <>{renderLoading?.() ?? null}</>;
  if (state.phase === "error") return <>{renderError?.(state.error, () => setAttempt((a) => a + 1)) ?? null}</>;
  return <ShellCtx.Provider value={value}>{children}</ShellCtx.Provider>;
}

export interface Resource<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** Context-scoped, cached GET (cache key includes tenant/company/period/permission hash). */
export function useResource<T>(endpoint: string | null, params: Record<string, string | number | null | undefined> = {}): Resource<T> {
  const shell = useShell();
  const { store, api } = shell;
  const ctxKey = `${shell.companyId}|${shell.period?.id}|${store.current.permissionHash}|${shell.switching ? "switching" : "ready"}`;
  const paramKey = JSON.stringify(params);
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<{ key: string; data: T | null; error: unknown; loading: boolean }>({ key: "", data: null, error: null, loading: true });
  const key = `${ctxKey}|${endpoint}|${paramKey}|${nonce}`;

  useEffect(() => {
    if (!endpoint) {
      setState({ key, data: null, error: null, loading: false });
      return;
    }
    if (shell.switching) {
      setState({ key, data: null, error: null, loading: true });
      return;
    }
    let alive = true;
    setState({ key, data: null, error: null, loading: true });
    store
      .fetch<T>(endpoint, { ...params, nonce }, (signal) => api.get<T>(endpoint, { params, signal }))
      .then((data) => alive && setState({ key, data, error: null, loading: false }))
      .catch((error) => {
        if (!alive || error instanceof StaleContextError || (error instanceof DOMException && error.name === "AbortError")) return;
        setState({ key, data: null, error, loading: false });
      });
    return () => {
      alive = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  // Never expose data computed for another context/params (stale render guard).
  const fresh = state.key === key;
  return { data: fresh ? state.data : null, error: fresh ? state.error : null, loading: !fresh || state.loading, reload: () => setNonce((n) => n + 1) };
}
