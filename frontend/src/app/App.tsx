// Application root (owner P08): router with the internal shell (/m/*) and the portal shell variant (/portal/*).
import { useMemo } from "react";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, type RouteObject } from "react-router";
import { createApiClient, type ApiClient } from "@lib/api/client";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";
import { ShellProvider } from "@shell/ShellContext";
import { AppShell } from "@shell/AppShell";
import { ModulePage, useCurrentModule } from "@shell/ModulePage";
import { NotFound } from "@shell/NotFound";
import { PortalProvider, PortalRoute } from "@shell/portal/PortalShell";
import { BootError, BootLoading } from "./Boot";

function InternalLayout() {
  const { module } = useCurrentModule();
  return (
    <AppShell module={module}>
      <Outlet />
    </AppShell>
  );
}

function InternalRoot({ api }: { api: ApiClient }) {
  return (
    <ShellProvider api={api} renderLoading={() => <BootLoading />} renderError={(e, retry) => <BootError error={e} retry={retry} />}>
      <Outlet />
    </ShellProvider>
  );
}

export function buildRoutes(api: ApiClient): RouteObject[] {
  return [
    { path: "/", element: <Navigate to="/m/inicio" replace /> },
    {
      element: <InternalRoot api={api} />,
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

export function App({ api: apiProp }: { api?: ApiClient }) {
  const api = useMemo(() => apiProp ?? createApiClient(), [apiProp]);
  const router = useMemo(() => createBrowserRouter(buildRoutes(api)), [api]);
  return (
    <DesignSystemProvider>
      <RouterProvider router={router} />
    </DesignSystemProvider>
  );
}
