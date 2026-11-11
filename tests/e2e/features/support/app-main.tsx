// P22c feature-e2e harness entry (browser side). DEV/test only, never shipped.
//
// It replaces `/src/app/main.tsx` of frontend/index.html (see stack.ts) and mounts the REAL application against the REAL API
// (in-process Django on loopback, PostgreSQL world built by the pytest runner). Two things differ from production:
//
// 1. Sign-in: instead of the Keycloak code flow the harness installs a synthetic signed-in AuthSession (`setAuth`, the
//    injection point P08 provides for tests). Its bearer tokens are minted by the backend's LocalIssuer for synthetic users
//    of the test tenant, so every API call still goes through the real OIDC verifier and the real P04 policy engine.
// 2. Page wiring (only when `augment` is set): pages that their owners delivered in their own folders but that are not
//    yet registered in the shell descriptor (CCR P09c-1, P11b-5, P13b, P09b) are added through the ShellProvider
//    `descriptors` prop. The real descriptors stay untouched; the missing registration is a separate strict-xfail defect
//    (tests/cross_module/test_xm_e2e_wiring.py) so the gap is never hidden.
import "virtual:nexus-tokens.css";
import "@ds/styles.css";
import { StrictMode, lazy, type ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router";
import { App } from "@app/App";
import { BootError, BootLoading } from "@app/Boot";
import { createApiClient } from "@lib/api/client";
import { setAuth, type AuthSession } from "@lib/auth/oidc";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { ShellProvider } from "@shell/ShellContext";
import { AppShell } from "@shell/AppShell";
import { ModulePage, useCurrentModule } from "@shell/ModulePage";
import { NotFound } from "@shell/NotFound";
import { discoverDescriptors, type LazyPage, type ModuleDescriptor } from "@shell/registry";

interface E2EConfig {
  tokens: Record<string, string>;
  user: string;
  augment?: boolean;
}

declare global {
  interface Window {
    __NEXUS_E2E__?: E2EConfig;
  }
}

const USER_KEY = "nx-e2e-user";

function config(): E2EConfig {
  const c = window.__NEXUS_E2E__;
  if (!c) throw new Error("e2e harness: window.__NEXUS_E2E__ missing (stack.ts injects it)");
  return c;
}

function currentUser(): string {
  let u: string | null = null;
  try {
    u = window.sessionStorage.getItem(USER_KEY);
  } catch {
    u = null;
  }
  return u ?? config().user;
}

const session: AuthSession = {
  accessToken: () => config().tokens[currentUser()] ?? null,
  isSignedIn: () => Boolean(config().tokens[currentUser()]),
  hasStoredSession: () => true,
  restore: async () => Boolean(config().tokens[currentUser()]),
  refresh: async () => false,
  signIn: async () => undefined,
  handleCallback: async () => "/m/inicio",
  logout: async () => undefined,
};
setAuth(session);

const page = <T extends string>(loader: () => Promise<Record<T, unknown>>, name: T): LazyPage =>
  lazy(() => loader().then((m) => ({ default: m[name] as ComponentType<never> }))) as unknown as LazyPage;

/** Delivered-but-unregistered pages (keys = registry "submenu/tab"). */
function extraPages(): Record<string, Record<string, LazyPage>> {
  return {
    inventario: {
      "movimientos/salidas": page(() => import("@features/inventory/issues/IssuesPage"), "default"),
      "maestros/conceptos-salida": page(() => import("@features/inventory/issues/IssueConceptsPage"), "default"),
      "produccion/recetas": page(() => import("@features/inventory/bom/RecipesPage"), "default"),
      "produccion/transformaciones": page(() => import("@features/inventory/transformations/TransformationsPage"), "default"),
      "produccion/plantillas-porcionado": page(() => import("@features/inventory/transformations/PortioningTemplatesPage"), "default"),
    },
    compras: {
      solicitudes: page(() => import("@features/purchasing/requisitions/RequisitionsPages"), "MyRequisitionsPage"),
      "solicitudes/mis-solicitudes": page(() => import("@features/purchasing/requisitions/RequisitionsPages"), "MyRequisitionsPage"),
      "solicitudes/por-aprobar": page(() => import("@features/purchasing/requisitions/RequisitionsPages"), "ApprovalInboxPage"),
      "solicitudes/consolidacion": page(() => import("@features/purchasing/requisitions/RequisitionsPages"), "ConsolidationPage"),
      "solicitudes/abastecimiento": page(() => import("@features/purchasing/requisitions/RequisitionsPages"), "SourcingPage"),
    },
    talento: {
      "nomina/conceptos": page(() => import("@features/payroll/ConceptsPage"), "ConceptsPage"),
    },
  };
}

function augmentedDescriptors(): ModuleDescriptor[] {
  const real = discoverDescriptors();
  const extra = extraPages();
  const out: ModuleDescriptor[] = real.map((d) => {
    const add = extra[d.id];
    // real registrations win: the harness only fills keys the owner has not registered
    return add ? { ...d, pages: { ...add, ...(d.pages ?? {}) } } : d;
  });
  for (const [id, pages] of Object.entries(extra)) {
    if (!real.some((d) => d.id === id)) out.push({ id, pages });
  }
  return out;
}

function AugmentedApp() {
  const api = createApiClient(undefined, "", session);
  const descriptors = augmentedDescriptors();
  function Layout() {
    const { module } = useCurrentModule();
    return (
      <AppShell module={module}>
        <Outlet />
      </AppShell>
    );
  }
  const router = createBrowserRouter([
    {
      element: (
        <ShellProvider api={api} descriptors={descriptors} renderLoading={() => <BootLoading />} renderError={(e, retry) => <BootError error={e} retry={retry} />}>
          <Outlet />
        </ShellProvider>
      ),
      children: [
        {
          path: "/m/:module",
          element: <Layout />,
          children: [
            { index: true, element: <ModulePage /> },
            { path: "*", element: <ModulePage /> },
          ],
        },
      ],
    },
    { path: "*", element: <NotFound home="/m/inicio" /> },
  ]);
  return (
    <DesignSystemProvider>
      <RouterProvider router={router} />
    </DesignSystemProvider>
  );
}

const el = document.getElementById("root");
if (el) {
  createRoot(el).render(<StrictMode>{config().augment ? <AugmentedApp /> : <App auth={session} />}</StrictMode>);
}
