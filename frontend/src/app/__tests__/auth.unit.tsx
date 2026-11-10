// FIX_UI_LOGIN_W03 app-level sign-in tests: entry screen, protected routes, callback history, bearer API client (UL-5/UL-6).
// Synthetic sessions and fake API only.
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { buildRoutes } from "@app/App";
import { fakeApi } from "@app/test/fakeApi";
import { fakeAuth } from "@app/test/auth";
import { ApiError, createApiClient, type Fetcher } from "@lib/api/client";
import { DesignSystemProvider } from "@ds/DesignSystemProvider";

function renderAt(path: string, auth = fakeAuth()) {
  const api = fakeApi();
  const router = createMemoryRouter(buildRoutes(createApiClient(api.fetcher, "", auth), auth), { initialEntries: [path] });
  render(
    <DesignSystemProvider theme="light">
      <RouterProvider router={router} />
    </DesignSystemProvider>,
  );
  return { router, api, auth };
}

describe("FIX_UI_LOGIN_W03 sign-in routes", () => {
  it("FIX_UI_LOGIN_W03 entry screen has the Iniciar sesión button and the DEV banner; the button starts sign-in", async () => {
    const auth = fakeAuth({ signedIn: false });
    renderAt("/", auth);
    const button = await screen.findByRole("button", { name: "Iniciar sesión" });
    expect(document.querySelector('[data-nx-region="dev-banner"]')).not.toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    button.click();
    await waitFor(() => expect(auth.signIns).toEqual(["/m/inicio"]));
  });

  it("FIX_UI_LOGIN_W03 / signed in goes to /m/inicio; a stored session is restored without the entry screen", async () => {
    const a = renderAt("/");
    await waitFor(() => expect(a.router.state.location.pathname).toBe("/m/inicio"));
    const restored = fakeAuth({ signedIn: false, stored: true, refreshOk: true });
    const b = renderAt("/", restored);
    await waitFor(() => expect(b.router.state.location.pathname).toBe("/m/inicio"));
    expect(restored.signIns).toEqual([]);
  });

  it("FIX_UI_LOGIN_W03 /m/* signed out redirects straight to the identity provider with the return path", async () => {
    const auth = fakeAuth({ signedIn: false });
    const { api } = renderAt("/m/contabilidad/mayor?x=1", auth);
    await waitFor(() => expect(auth.signIns).toEqual(["/m/contabilidad/mayor?x=1"]));
    expect(await screen.findByText("Redirigiendo al inicio de sesión…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Iniciar sesión" })).toBeNull();
    expect(api.calls.filter((c) => c.url.startsWith("/api/v1/ui"))).toEqual([]); // no API call before sign-in
  });

  it("FIX_UI_LOGIN_W03 callback replaces history with the return path", async () => {
    const auth = fakeAuth({ signedIn: false, callbackPath: "/m/aprobaciones" });
    const { router } = renderAt("/oidc/callback?code=c&state=s", auth);
    await waitFor(() => expect(router.state.location.pathname).toBe("/m/aprobaciones"));
    expect(router.state.historyAction).toBe("REPLACE");
  });

  it("FIX_UI_LOGIN_W03 callback shows Conectando… with the DEV banner while the code is exchanged", async () => {
    const auth = fakeAuth({ signedIn: false });
    auth.handleCallback = () => new Promise<string>(() => undefined);
    renderAt("/oidc/callback?code=c&state=s", auth);
    expect(await screen.findByText("Conectando…")).toBeInTheDocument();
    expect(document.querySelector('[data-nx-region="dev-banner"]')).not.toBeNull();
  });

  it("FIX_UI_LOGIN_W03 callback failure shows an error card with Volver a intentar (no automatic loop)", async () => {
    const auth = fakeAuth({ signedIn: false });
    auth.handleCallback = () => Promise.reject(new Error("state_mismatch"));
    renderAt("/oidc/callback?code=c&state=forged", auth);
    const retry = await screen.findByRole("button", { name: "Volver a intentar" });
    expect(screen.getByText("No fue posible iniciar sesión")).toBeInTheDocument();
    expect(document.querySelector('[data-nx-region="dev-banner"]')).not.toBeNull();
    expect(auth.signIns).toEqual([]);
    retry.click();
    await waitFor(() => expect(auth.signIns).toEqual(["/m/inicio"]));
  });

  it("FIX_UI_LOGIN_W03 Cerrar sesión logs out through the sign-in session", async () => {
    const auth = fakeAuth();
    renderAt("/m/inicio", auth);
    await screen.findByRole("navigation", { name: "Navegación de módulos" });
    expect(auth.logouts).toBe(0);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Menú de usuario/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Cerrar sesión" }));
    await waitFor(() => expect(auth.logouts).toBe(1));
  });
});

describe("FIX_UI_LOGIN_W03 bearer API client", () => {
  const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  const unauthorized = () => new Response(JSON.stringify({ code: "not_authenticated", status: 401 }), { status: 401, headers: { "Content-Type": "application/problem+json" } });

  it("FIX_UI_LOGIN_W03 sends Authorization: Bearer; 401 → one refresh → one retry with the new token", async () => {
    const auth = fakeAuth({ refreshOk: true, token: "t1" });
    const seen: Array<string | undefined> = [];
    const fetcher: Fetcher = async (_i, init) => {
      const h = init?.headers as Record<string, string>;
      seen.push(h.Authorization);
      if (seen.length === 1) {
        auth.token = "t2"; // the refresh below yields a new access token
        return unauthorized();
      }
      return ok({ fine: true });
    };
    const client = createApiClient(fetcher, "", auth);
    await expect(client.post("/api/v1/x", { body: { a: 1 }, idempotencyKey: "k-1" })).resolves.toEqual({ fine: true });
    expect(seen).toEqual(["Bearer t1", "Bearer t2"]);
    expect(auth.refreshes).toBe(1);
    expect(auth.signIns).toEqual([]);
  });

  it("FIX_UI_LOGIN_W03 a second 401 sends the browser to sign-in with the return URL", async () => {
    const auth = fakeAuth({ refreshOk: true });
    let n = 0;
    const client = createApiClient(async () => (n++, unauthorized()), "", auth);
    const err = await client.get("/api/v1/ui/context").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect(n).toBe(2);
    expect(auth.refreshes).toBe(1);
    expect(auth.signIns).toHaveLength(1);
    expect(auth.signIns[0]).toBe(window.location.pathname + window.location.search + window.location.hash);
  });

  it("FIX_UI_LOGIN_W03 a failed refresh also goes to sign-in; no session → no Authorization header", async () => {
    const auth = fakeAuth({ refreshOk: false });
    const client = createApiClient(async () => unauthorized(), "", auth);
    await expect(client.get("/api/v1/x")).rejects.toBeInstanceOf(ApiError);
    expect(auth.signIns).toHaveLength(1);
    let header: string | undefined = "unset";
    const anon = createApiClient(async (_i, init) => {
      header = (init?.headers as Record<string, string>).Authorization;
      return ok({});
    });
    await anon.get("/api/v1/x");
    expect(header).toBeUndefined();
  });

  it("FIX_UI_LOGIN_W03 BootError is not shown for an auth redirect; it still shows the trace id otherwise", async () => {
    const auth = fakeAuth();
    const api = fakeApi({
      routes: { "/api/v1/ui/modules": () => new Response(JSON.stringify({ code: "service_unavailable", status: 503, trace_id: "trace-503" }), { status: 503 }) },
    });
    const router = createMemoryRouter(buildRoutes(createApiClient(api.fetcher, "", auth), auth), { initialEntries: ["/m/inicio"] });
    render(
      <DesignSystemProvider theme="light">
        <RouterProvider router={router} />
      </DesignSystemProvider>,
    );
    expect(await screen.findByText("Código de seguimiento: trace-503")).toBeInTheDocument();
  });
});
