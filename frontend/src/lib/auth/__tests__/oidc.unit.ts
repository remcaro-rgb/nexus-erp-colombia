// FIX_UI_LOGIN_W03 unit tests of the SPA OIDC client (Authorization Code + PKCE S256, public client erp-web).
// Synthetic IdP responses only (no network, no real identity).
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthError, OidcAuth, PENDING_KEY, REFRESH_KEY, buildAuthorizeUrl, safeReturnPath } from "@lib/auth/oidc";
import { base64UrlEncode, decodeJwtPayload, pkceChallenge, randomString } from "@lib/auth/pkce";

const ORIGIN = "http://127.0.0.1:8080";
const ISSUER = `${ORIGIN}/auth/realms/erp-dev`;
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/protocol/openid-connect/auth`,
  token_endpoint: `${ISSUER}/protocol/openid-connect/token`,
  end_session_endpoint: `${ISSUER}/protocol/openid-connect/logout`,
};

const enc = (o: unknown) => base64UrlEncode(new TextEncoder().encode(JSON.stringify(o)));
export function fakeIdJwt(claims: Record<string, unknown>): string {
  return `${enc({ alg: "none", typ: "JWT" })}.${enc({ iss: ISSUER, aud: "erp-web", azp: "erp-web", exp: Math.floor(Date.now() / 1000) + 600, ...claims })}.sig`;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function setup(token: (form: URLSearchParams) => Response = () => json({ error: "invalid_grant" }, 400), discovery: unknown = DISCOVERY) {
  const navigations: string[] = [];
  const posts: URLSearchParams[] = [];
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith("/.well-known/openid-configuration")) return json(discovery);
    if (input === DISCOVERY.token_endpoint && init?.method === "POST") {
      const form = new URLSearchParams(String(init.body));
      posts.push(form);
      return token(form);
    }
    return json({}, 404);
  });
  const auth = new OidcAuth({ origin: ORIGIN, fetcher, storage: window.sessionStorage, navigate: (u) => navigations.push(u) });
  return { auth, fetcher, navigations, posts };
}

async function startSignIn(auth: OidcAuth, navigations: string[], returnPath = "/m/contabilidad") {
  await auth.signIn(returnPath);
  const url = new URL(navigations[navigations.length - 1] ?? "");
  const pending = JSON.parse(window.sessionStorage.getItem(PENDING_KEY) ?? "{}") as { state: string; nonce: string; code_verifier: string };
  return { url, pending };
}

afterEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe("FIX_UI_LOGIN_W03 PKCE and authorize request", () => {
  it("FIX_UI_LOGIN_W03 PKCE S256 matches the RFC 7636 appendix B test vector", async () => {
    expect(await pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
    const v = randomString(32);
    expect(v).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(randomString(32)).not.toBe(v);
  });

  it("FIX_UI_LOGIN_W03 authorize URL carries code + PKCE S256 + state + nonce for erp-web, same origin", async () => {
    const { auth, navigations } = setup();
    const { url, pending } = await startSignIn(auth, navigations);
    expect(url.origin + url.pathname).toBe(DISCOVERY.authorization_endpoint);
    const p = url.searchParams;
    expect(p.get("client_id")).toBe("erp-web");
    expect(p.get("response_type")).toBe("code");
    expect(p.get("scope")).toBe("openid profile email");
    expect(p.get("redirect_uri")).toBe(`${ORIGIN}/oidc/callback`);
    expect(p.get("code_challenge_method")).toBe("S256");
    expect(p.get("state")).toBe(pending.state);
    expect(p.get("nonce")).toBe(pending.nonce);
    expect(p.get("code_challenge")).toBe(await pkceChallenge(pending.code_verifier));
    expect(p.has("client_secret")).toBe(false);
    expect(buildAuthorizeUrl(DISCOVERY.authorization_endpoint, { clientId: "erp-web", redirectUri: "r", state: "s", nonce: "n", codeChallenge: "c" })).toContain("code_challenge_method=S256");
  });

  it("FIX_UI_LOGIN_W03 realm endpoints published with Keycloak's internal host are re-based onto this origin", async () => {
    const posts: string[] = [];
    const internal = { ...DISCOVERY, token_endpoint: `http://localhost/auth/realms/erp-dev/protocol/openid-connect/token` };
    const auth = new OidcAuth({
      origin: ORIGIN,
      storage: window.sessionStorage,
      navigate: () => undefined,
      fetcher: async (input) => {
        if (input.endsWith("/.well-known/openid-configuration")) return json(internal);
        posts.push(input);
        return json({}, 400);
      },
    });
    expect((await auth.discover()).token_endpoint).toBe(DISCOVERY.token_endpoint);
    window.sessionStorage.setItem(REFRESH_KEY, "not-a-secret-refresh-0");
    await auth.refresh();
    expect(posts).toEqual([DISCOVERY.token_endpoint]);
  });

  it("FIX_UI_LOGIN_W03 discovery from another issuer or with endpoints outside the realm is refused", async () => {
    for (const bad of [{ ...DISCOVERY, issuer: "http://example.invalid/auth/realms/erp-dev" }, { ...DISCOVERY, token_endpoint: "http://example.invalid/token" }, { ...DISCOVERY, authorization_endpoint: "javascript:alert(1)" }]) {
      const { auth, navigations } = setup(undefined, bad);
      await expect(auth.signIn("/m/inicio")).rejects.toMatchObject({ code: "discovery_failed" });
      expect(navigations).toEqual([]);
    }
  });

  it("FIX_UI_LOGIN_W03 return paths stay on this origin and never point at the callback", () => {
    expect(safeReturnPath("/m/ventas?x=1#y")).toBe("/m/ventas?x=1#y");
    for (const bad of ["//example.invalid/x", "https://example.invalid", "/oidc/callback?code=1", "/", "", null, "/\\evil"]) expect(safeReturnPath(bad)).toBe("/m/inicio");
  });
});

describe("FIX_UI_LOGIN_W03 callback", () => {
  it("FIX_UI_LOGIN_W03 state mismatch is rejected and the pending request is consumed (no loop)", async () => {
    const { auth, navigations, posts } = setup();
    await startSignIn(auth, navigations);
    await expect(auth.handleCallback("?code=abc&state=forged")).rejects.toMatchObject({ code: "state_mismatch" });
    expect(posts).toHaveLength(0);
    expect(window.sessionStorage.getItem(PENDING_KEY)).toBeNull();
    expect(auth.isSignedIn()).toBe(false);
  });

  it("FIX_UI_LOGIN_W03 nonce mismatch is rejected (no tokens kept)", async () => {
    const { auth, navigations } = setup(() => json({ access_token: "at", refresh_token: "rt", expires_in: 600, id_token: fakeIdJwt({ nonce: "other" }) }));
    const { pending } = await startSignIn(auth, navigations);
    const err = await auth.handleCallback(`?code=abc&state=${pending.state}`).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AuthError);
    expect((err as AuthError).code).toBe("nonce_mismatch");
    expect(auth.accessToken()).toBeNull();
    expect(window.sessionStorage.getItem(REFRESH_KEY)).toBeNull();
  });

  it("FIX_UI_LOGIN_W03 IdP error and wrong audience are rejected", async () => {
    const a = setup();
    await startSignIn(a.auth, a.navigations);
    await expect(a.auth.handleCallback("?error=access_denied")).rejects.toMatchObject({ code: "idp_error" });
    let nonce = "";
    const b = setup(() => json({ access_token: "at", expires_in: 600, id_token: fakeIdJwt({ nonce, aud: "erp-portal", azp: "erp-portal" }) }));
    const { pending } = await startSignIn(b.auth, b.navigations);
    nonce = pending.nonce;
    await expect(b.auth.handleCallback(`?code=abc&state=${pending.state}`)).rejects.toMatchObject({ code: "invalid_id_token" });
  });

  it("FIX_UI_LOGIN_W03 code exchange keeps the access token in memory and only the refresh token in sessionStorage", async () => {
    let nonce = "";
    const { auth, navigations, posts } = setup(() => json({ access_token: "not-a-secret-access-1", refresh_token: "not-a-secret-refresh-1", expires_in: 600, id_token: fakeIdJwt({ nonce }) }));
    const { pending } = await startSignIn(auth, navigations, "/m/aprobaciones");
    nonce = pending.nonce;
    await expect(auth.handleCallback(`?code=abc&state=${pending.state}&session_state=x`)).resolves.toBe("/m/aprobaciones");
    const form = posts[0];
    expect(form?.get("grant_type")).toBe("authorization_code");
    expect(form?.get("code_verifier")).toBe(pending.code_verifier);
    expect(form?.get("client_id")).toBe("erp-web");
    expect(form?.get("redirect_uri")).toBe(`${ORIGIN}/oidc/callback`);
    expect(form?.has("client_secret")).toBe(false);
    expect(auth.accessToken()).toBe("not-a-secret-access-1");
    expect(window.sessionStorage.getItem(REFRESH_KEY)).toBe("not-a-secret-refresh-1");
    expect(window.sessionStorage.getItem(PENDING_KEY)).toBeNull();
    expect([...Array(window.sessionStorage.length).keys()].map((i) => window.sessionStorage.key(i))).toEqual([REFRESH_KEY]);
    expect(JSON.stringify(window.sessionStorage.getItem(REFRESH_KEY))).not.toContain("not-a-secret-access-1");
  });

  it("FIX_UI_LOGIN_W03 no auth key is ever written to localStorage (sign-in, callback, refresh, logout)", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem");
    let nonce = "";
    const { auth, navigations } = setup(() => json({ access_token: "at", refresh_token: "rt", expires_in: 600, id_token: fakeIdJwt({ nonce }) }));
    const { pending } = await startSignIn(auth, navigations);
    nonce = pending.nonce;
    await auth.handleCallback(`?code=abc&state=${pending.state}`);
    await auth.refresh();
    await auth.logout();
    expect(window.localStorage.length).toBe(0);
    for (const call of spy.mock.contexts) expect(call).not.toBe(window.localStorage);
    spy.mockRestore();
  });
});

describe("FIX_UI_LOGIN_W03 session restore, refresh and logout", () => {
  it("FIX_UI_LOGIN_W03 a full page load restores silently from the stored refresh token", async () => {
    window.sessionStorage.setItem(REFRESH_KEY, "rt-1");
    const { auth, posts } = setup(() => json({ access_token: "at-2", refresh_token: "rt-2", expires_in: 600, id_token: fakeIdJwt({}) }));
    expect(auth.hasStoredSession()).toBe(true);
    const [a, b] = await Promise.all([auth.restore(), auth.restore()]);
    expect(a && b).toBe(true);
    expect(posts).toHaveLength(1); // one refresh grant even for concurrent restores (refresh tokens are single use)
    expect(posts[0]?.get("grant_type")).toBe("refresh_token");
    expect(auth.accessToken()).toBe("at-2");
    expect(window.sessionStorage.getItem(REFRESH_KEY)).toBe("rt-2");
  });

  it("FIX_UI_LOGIN_W03 a rejected refresh clears the session", async () => {
    window.sessionStorage.setItem(REFRESH_KEY, "rt-old");
    const { auth } = setup();
    await expect(auth.restore()).resolves.toBe(false);
    expect(auth.hasStoredSession()).toBe(false);
  });

  it("FIX_UI_LOGIN_W03 RP-initiated logout uses id_token_hint, or client_id after a reload", async () => {
    window.sessionStorage.setItem(REFRESH_KEY, "rt-1");
    const idToken = fakeIdJwt({});
    const s = setup(() => json({ access_token: "at", refresh_token: "rt-2", expires_in: 600, id_token: idToken }));
    await s.auth.restore();
    await s.auth.logout();
    const out = new URL(s.navigations[0] ?? "");
    expect(out.origin + out.pathname).toBe(DISCOVERY.end_session_endpoint);
    expect(out.searchParams.get("post_logout_redirect_uri")).toBe(`${ORIGIN}/`);
    expect(out.searchParams.get("id_token_hint")).toBe(idToken);
    expect(s.auth.accessToken()).toBeNull();
    expect(window.sessionStorage.getItem(REFRESH_KEY)).toBeNull();
    const r = setup();
    await r.auth.logout();
    const out2 = new URL(r.navigations[0] ?? "");
    expect(out2.searchParams.get("client_id")).toBe("erp-web");
    expect(out2.searchParams.has("id_token_hint")).toBe(false);
  });

  it("FIX_UI_LOGIN_W03 decodes only well-formed id_token payloads", () => {
    expect(decodeJwtPayload(fakeIdJwt({ nonce: "n" }))?.nonce).toBe("n");
    expect(decodeJwtPayload("not-a-jwt")).toBeNull();
    expect(decodeJwtPayload("a.!!!.c")).toBeNull();
  });
});
