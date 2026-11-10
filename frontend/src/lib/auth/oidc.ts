// OIDC sign-in for the internal SPA (owner P08, FIX-UI-LOGIN-W03 UL-5; CCR P08-6).
// Authorization Code + PKCE (S256) against the PUBLIC Keycloak client `erp-web` of realm `erp-dev`, same origin
// (`/auth/*` is proxied to Keycloak). No client secret, no password grant, no implicit flow, no dependency.
//
// Storage:
// * access token + id_token: memory only;
// * refresh token: sessionStorage (restores the session on a full page load of the same tab);
// * PKCE code_verifier, state, nonce and return path: sessionStorage only between the authorize redirect and the
//   callback, removed when the callback runs (success or failure: a failed callback never loops).
// Refresh ~60 s before the access token expires; RP-initiated logout at end_session_endpoint.
import { decodeJwtPayload, pkceChallenge, randomString } from "./pkce";

export const OIDC_CLIENT_ID = "erp-web";
export const OIDC_REALM = "erp-dev";
/** SPA callback. Not under /auth/* (proxied to Keycloak) and not matching /(login|auth)\b/. */
export const CALLBACK_PATH = "/oidc/callback";
export const DEFAULT_RETURN_PATH = "/m/inicio";
export const OIDC_SCOPE = "openid profile email";
/** sessionStorage keys (the only two places auth state is persisted). */
export const PENDING_KEY = "nexus.oidc.pending";
export const REFRESH_KEY = "nexus.oidc.refresh";
const REFRESH_LEAD_MS = 60_000;
const MAX_TIMER_MS = 2_147_000_000;

export type AuthErrorCode = "state_mismatch" | "nonce_mismatch" | "idp_error" | "no_pending_request" | "token_error" | "invalid_id_token" | "discovery_failed";

export class AuthError extends Error {
  constructor(public readonly code: AuthErrorCode, message?: string) {
    super(message ?? code);
    this.name = "AuthError";
  }
}

/** What the app, the API client and the shell need from a sign-in session (tests inject a fake one). */
export interface AuthSession {
  /** Current access token (memory) or null. */
  accessToken(): string | null;
  isSignedIn(): boolean;
  /** A refresh token is stored for this tab (a silent restore can be attempted). */
  hasStoredSession(): boolean;
  /** Signed in, or silently restored from the stored refresh token. */
  restore(): Promise<boolean>;
  /** Refresh grant; false when the session is gone. */
  refresh(): Promise<boolean>;
  /** Start the authorize redirect; `returnPath` is restored after the callback. */
  signIn(returnPath?: string): Promise<void>;
  /** Exchange the callback's code; resolves to the return path. */
  handleCallback(search: string): Promise<string>;
  /** Clear tokens and perform RP-initiated logout. */
  logout(): Promise<void>;
}

export interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

interface TokenSet {
  accessToken: string;
  idToken: string | null;
  expiresAt: number;
}

interface Pending {
  code_verifier: string;
  state: string;
  nonce: string;
  return_path: string;
}

export interface AuthStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface OidcAuthOptions {
  origin?: string;
  fetcher?: (input: string, init?: RequestInit) => Promise<Response>;
  storage?: AuthStorage;
  navigate?: (url: string) => void;
  now?: () => number;
  clientId?: string;
  realm?: string;
}

/** Same-origin path only (no scheme, no `//host`, never the callback itself). */
export function safeReturnPath(path: string | null | undefined): string {
  if (!path || !path.startsWith("/") || path.startsWith("//") || path.startsWith("/\\")) return DEFAULT_RETURN_PATH;
  if (path === "/" || path.startsWith(CALLBACK_PATH)) return DEFAULT_RETURN_PATH;
  return path;
}

export function buildAuthorizeUrl(
  endpoint: string,
  p: { clientId: string; redirectUri: string; state: string; nonce: string; codeChallenge: string },
): string {
  const url = new URL(endpoint);
  url.searchParams.set("client_id", p.clientId);
  url.searchParams.set("redirect_uri", p.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", OIDC_SCOPE);
  url.searchParams.set("state", p.state);
  url.searchParams.set("nonce", p.nonce);
  url.searchParams.set("code_challenge", p.codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

function browserStorage(): AuthStorage {
  try {
    return window.sessionStorage;
  } catch {
    const mem = new Map<string, string>();
    return { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => void mem.set(k, v), removeItem: (k) => void mem.delete(k) };
  }
}

export class OidcAuth implements AuthSession {
  readonly origin: string;
  readonly clientId: string;
  readonly issuer: string;
  readonly redirectUri: string;
  private readonly fetcher: NonNullable<OidcAuthOptions["fetcher"]>;
  private readonly storage: AuthStorage;
  private readonly navigate: (url: string) => void;
  private readonly now: () => number;
  private tokens: TokenSet | null = null;
  private discovery: Promise<Discovery> | null = null;
  private refreshing: Promise<boolean> | null = null;
  private callback: Promise<string> | null = null;
  private redirecting = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(opts: OidcAuthOptions = {}) {
    this.origin = opts.origin ?? window.location.origin;
    this.clientId = opts.clientId ?? OIDC_CLIENT_ID;
    this.issuer = `${this.origin}/auth/realms/${opts.realm ?? OIDC_REALM}`;
    this.redirectUri = `${this.origin}${CALLBACK_PATH}`;
    this.fetcher = opts.fetcher ?? ((i, init) => fetch(i, init));
    this.storage = opts.storage ?? browserStorage();
    this.navigate = opts.navigate ?? ((url) => window.location.assign(url));
    this.now = opts.now ?? (() => Date.now());
  }

  accessToken(): string | null {
    const t = this.tokens;
    return t && this.now() < t.expiresAt - 5_000 ? t.accessToken : null;
  }

  isSignedIn(): boolean {
    return this.accessToken() !== null;
  }

  hasStoredSession(): boolean {
    return Boolean(this.storage.getItem(REFRESH_KEY));
  }

  async restore(): Promise<boolean> {
    if (this.isSignedIn()) return true;
    if (!this.hasStoredSession()) return false;
    return this.refresh();
  }

  /** Discovery from the same origin; the issuer must be this origin's realm and every endpoint same-origin. */
  discover(): Promise<Discovery> {
    this.discovery ??= (async () => {
      const res = await this.fetcher(`${this.issuer}/.well-known/openid-configuration`, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!res.ok) throw new AuthError("discovery_failed", `HTTP ${res.status}`);
      const d = (await res.json()) as Discovery;
      const sameOrigin = (u: string | undefined) => {
        try {
          return u === undefined || new URL(u).origin === this.origin;
        } catch {
          return false;
        }
      };
      if (d.issuer !== this.issuer || !d.authorization_endpoint || !d.token_endpoint || ![d.authorization_endpoint, d.token_endpoint, d.end_session_endpoint].every(sameOrigin)) {
        throw new AuthError("discovery_failed", "unexpected issuer or endpoints");
      }
      return d;
    })().catch((e: unknown) => {
      this.discovery = null;
      throw e instanceof AuthError ? e : new AuthError("discovery_failed");
    });
    return this.discovery;
  }

  async signIn(returnPath?: string): Promise<void> {
    if (this.redirecting) return;
    this.redirecting = true;
    try {
      const d = await this.discover();
      const pending: Pending = { code_verifier: randomString(32), state: randomString(16), nonce: randomString(16), return_path: safeReturnPath(returnPath) };
      const codeChallenge = await pkceChallenge(pending.code_verifier);
      this.storage.setItem(PENDING_KEY, JSON.stringify(pending));
      this.navigate(buildAuthorizeUrl(d.authorization_endpoint, { clientId: this.clientId, redirectUri: this.redirectUri, state: pending.state, nonce: pending.nonce, codeChallenge }));
    } catch (e) {
      this.redirecting = false;
      throw e;
    }
  }

  handleCallback(search: string): Promise<string> {
    this.callback ??= this.exchange(search);
    return this.callback;
  }

  private async exchange(search: string): Promise<string> {
    const params = new URLSearchParams(search);
    const raw = this.storage.getItem(PENDING_KEY);
    this.storage.removeItem(PENDING_KEY); // one shot: a replayed or failed callback never loops
    if (params.get("error")) throw new AuthError("idp_error", params.get("error") ?? undefined);
    let pending: Pending | null = null;
    try {
      pending = raw ? (JSON.parse(raw) as Pending) : null;
    } catch {
      pending = null;
    }
    if (!pending?.state || !pending.code_verifier) throw new AuthError("no_pending_request");
    if (params.get("state") !== pending.state) throw new AuthError("state_mismatch");
    const code = params.get("code");
    if (!code) throw new AuthError("idp_error", "missing code");
    const d = await this.discover();
    const body = await this.tokenRequest(d, {
      grant_type: "authorization_code",
      code,
      redirect_uri: this.redirectUri,
      client_id: this.clientId,
      code_verifier: pending.code_verifier,
    });
    if (!body) throw new AuthError("token_error");
    this.accept(body, d, pending.nonce);
    return safeReturnPath(pending.return_path);
  }

  refresh(): Promise<boolean> {
    this.refreshing ??= (async () => {
      const rt = this.storage.getItem(REFRESH_KEY);
      if (!rt) return false;
      try {
        const d = await this.discover();
        const body = await this.tokenRequest(d, { grant_type: "refresh_token", refresh_token: rt, client_id: this.clientId });
        if (!body) {
          this.clear();
          return false;
        }
        this.accept(body, d, null);
        return true;
      } catch {
        this.clear();
        return false;
      }
    })().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  async logout(): Promise<void> {
    const idToken = this.tokens?.idToken ?? null;
    this.clear();
    let d: Discovery | null = null;
    try {
      d = await this.discover();
    } catch {
      d = null;
    }
    if (!d?.end_session_endpoint) {
      this.navigate(`${this.origin}/`);
      return;
    }
    const url = new URL(d.end_session_endpoint);
    url.searchParams.set("post_logout_redirect_uri", `${this.origin}/`);
    if (idToken) url.searchParams.set("id_token_hint", idToken);
    else url.searchParams.set("client_id", this.clientId);
    this.navigate(url.toString());
  }

  private async tokenRequest(d: Discovery, form: Record<string, string>): Promise<Record<string, unknown> | null> {
    const res = await this.fetcher(d.token_endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: new URLSearchParams(form).toString(),
      credentials: "same-origin",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Record<string, unknown>;
    return typeof body.access_token === "string" ? body : null;
  }

  /** Validate the id_token (iss, aud/azp, exp, nonce on the code exchange) and keep the tokens. */
  private accept(body: Record<string, unknown>, d: Discovery, nonce: string | null): void {
    const idToken = typeof body.id_token === "string" ? body.id_token : null;
    if (nonce !== null && !idToken) throw new AuthError("invalid_id_token", "missing id_token");
    if (idToken) {
      const claims = decodeJwtPayload(idToken);
      if (!claims) throw new AuthError("invalid_id_token");
      const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
      if (claims.iss !== d.issuer) throw new AuthError("invalid_id_token", "issuer");
      if (!aud.includes(this.clientId) || (claims.azp !== undefined && claims.azp !== this.clientId)) throw new AuthError("invalid_id_token", "audience");
      if (typeof claims.exp !== "number" || claims.exp * 1000 <= this.now()) throw new AuthError("invalid_id_token", "expired");
      if (nonce !== null && claims.nonce !== nonce) throw new AuthError("nonce_mismatch");
    }
    const expiresIn = typeof body.expires_in === "number" ? body.expires_in : 300;
    this.tokens = { accessToken: body.access_token as string, idToken: idToken ?? this.tokens?.idToken ?? null, expiresAt: this.now() + expiresIn * 1000 };
    if (typeof body.refresh_token === "string") this.storage.setItem(REFRESH_KEY, body.refresh_token);
    this.redirecting = false;
    this.schedule();
  }

  private schedule(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!this.tokens) return;
    const delay = Math.min(Math.max(this.tokens.expiresAt - this.now() - REFRESH_LEAD_MS, 1_000), MAX_TIMER_MS);
    this.timer = setTimeout(() => void this.refresh(), delay);
  }

  private clear(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.tokens = null;
    this.storage.removeItem(REFRESH_KEY);
    this.storage.removeItem(PENDING_KEY);
  }
}

let current: AuthSession | null = null;

/** The app's sign-in session (created on first use in the browser). */
export function getAuth(): AuthSession {
  current ??= new OidcAuth();
  return current;
}

/** Replace the app's session (dependency injection: unit tests install a synthetic signed-in session). */
export function setAuth(session: AuthSession | null): void {
  current = session;
}

/** Current location as a return path (pathname + search + hash). */
export function currentPath(): string {
  try {
    return window.location.pathname + window.location.search + window.location.hash;
  } catch {
    return DEFAULT_RETURN_PATH;
  }
}
