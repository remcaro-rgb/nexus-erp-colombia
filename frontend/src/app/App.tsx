// Application root (owner P08): router with the sign-in routes ("/", /oidc/callback), the internal shell (/m/*, signed in
// only: FIX-UI-LOGIN-W03 UL-5) and the portal shell variant (/portal/*).
import { useMemo } from "react";
import { createBrowserRouter, Outlet, RouterProvider, type RouteObject } from "react-router";
import { createApiClient, type ApiClient } from "@lib/api/client";
import { CALLBACK_PATH, getAuth, type AuthSession } from "@lib/auth/oidc";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { ShellProvider } from "@shell/ShellContext";
import { AppShell } from "@shell/AppShell";
import { ModulePage, useCurrentModule } from "@shell/ModulePage";
import { NotFound } from "@shell/NotFound";
import { PortalProvider, PortalRoute } from "@shell/portal/PortalShell";
import { CallbackRoute, EntryRoute, RequireAuth } from "./AuthRoutes";
import { BootError, BootLoading } from "./Boot";

function InternalLayout() {
  const { module } = useCurrentModule();
  return (
    <AppShell module={module}>
      <Outlet />
    </AppShell>
  );
}

function InternalRoot({ api, auth }: { api: ApiClient; auth: AuthSession }) {
  return (
    <RequireAuth auth={auth}>
      <ShellProvider
        api={api}
        renderLoading={() => <BootLoading />}
        renderError={(e, retry) => <BootError error={e} retry={retry} />}
        onLogout={() => void auth.logout()}
      >
        <Outlet />
      </ShellProvider>
    </RequireAuth>
  );
}

/** Routes of the app. `auth` defaults to the browser sign-in session (tests install a synthetic one with setAuth). */
export function buildRoutes(api: ApiClient, auth: AuthSession = getAuth()): RouteObject[] {
  return [
    { path: "/", element: <EntryRoute auth={auth} /> },
    { path: CALLBACK_PATH, element: <CallbackRoute auth={auth} /> },
    {
      element: <InternalRoot api={api} auth={auth} />,
      children: [
        {
          path: "/m/:module",
          element: <InternalLayout />,
          children: [
            { index: true, element: <ModulePage /> },
            { path: "*", element: <ModulePage /> },
          ],
        },
      ],
    },
    {
      path: "/portal/:portal/*",
      element: (
        <PortalProvider api={api}>
          <PortalRoute />
        </PortalProvider>
      ),
    },
    { path: "*", element: <NotFound home="/" /> },
  ];
}

export function App({ api: apiProp, auth: authProp }: { api?: ApiClient; auth?: AuthSession }) {
  const auth = useMemo(() => authProp ?? getAuth(), [authProp]);
  const api = useMemo(() => apiProp ?? createApiClient(undefined, "", auth), [apiProp, auth]);
  const router = useMemo(() => createBrowserRouter(buildRoutes(api, auth)), [api, auth]);
  return (
    <DesignSystemProvider>
      <RouterProvider router={router} />
    </DesignSystemProvider>
  );
}
