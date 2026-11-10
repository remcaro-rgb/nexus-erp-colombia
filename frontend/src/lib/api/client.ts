// HTTP client for the public API (owner P08). The UI uses only the public API (constitution).
// * company_id / period_id travel as explicit params (module-registry api.headers); the server re-checks membership.
// * Every state-changing call sends an Idempotency-Key.
// * Errors are RFC 9457 problems mapped to ApiError (field errors by JSON pointer, trace id for the toast).
// * Requests can be aborted (context switch) and responses from a stale context are dropped by the caller.
// * With a sign-in session (optional third argument, FIX-UI-LOGIN-W03 UL-6): `Authorization: Bearer <access token>`;
//   a 401 refreshes once and retries once; when that fails the browser is sent to sign-in with the return URL.
import type { Problem } from "./types";
import { newIdempotencyKey } from "../ids";
import { currentPath, type AuthSession } from "../auth/oidc";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly problem: Problem | null,
    public readonly traceId: string | null,
  ) {
    super(problem?.detail || problem?.title || `HTTP ${status}`);
    this.name = "ApiError";
  }

  /** { "/field": "detail" } from problem.errors[] */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.problem?.errors ?? []) {
      if (e.pointer) out[e.pointer.replace(/^#?\/?/, "")] = e.detail ?? e.code ?? "";
    }
    return out;
  }
}

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export interface RequestOptions {
  params?: Record<string, string | number | boolean | null | undefined>;
  body?: unknown;
  signal?: AbortSignal;
  /** Required for POST/PUT/PATCH/DELETE (generated when absent). */
  idempotencyKey?: string;
  ifMatch?: string;
  headers?: Record<string, string>;
}

export interface ApiClient {
  get<T>(path: string, opts?: RequestOptions): Promise<T>;
  post<T>(path: string, opts?: RequestOptions): Promise<T>;
  patch<T>(path: string, opts?: RequestOptions): Promise<T>;
}

export function buildUrl(path: string, params?: RequestOptions["params"]): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== null && v !== undefined && v !== "") qs.set(k, String(v));
  }
  const q = qs.toString();
  return q ? `${path}${path.includes("?") ? "&" : "?"}${q}` : path;
}

/** The error the client throws after a failed re-authentication (the sign-in redirect is under way). */
export function isAuthRedirect(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function createApiClient(fetcher: Fetcher = (i, init) => fetch(i, init), base = "", auth?: AuthSession | null): ApiClient {
  async function call<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json, application/problem+json", ...opts.headers };
    if (method !== "GET") headers["Idempotency-Key"] = opts.idempotencyKey ?? newIdempotencyKey();
    if (opts.ifMatch) headers["If-Match"] = opts.ifMatch;
    let body: string | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const send = () => {
      const token = auth?.accessToken();
      const h = token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
      return fetcher(base + buildUrl(path, opts.params), { method, headers: h, body, signal: opts.signal, credentials: "same-origin" });
    };
    let res = await send();
    if (res.status === 401 && auth) {
      // One refresh, one retry (same Idempotency-Key); then sign in again and come back here.
      const refreshed = await auth.refresh().catch(() => false);
      if (refreshed) res = await send();
      if (res.status === 401) void auth.signIn(currentPath()).catch(() => undefined);
    }
    const traceId = res.headers.get("X-Request-Id") ?? res.headers.get("traceparent");
    if (!res.ok) {
      let problem: Problem | null = null;
      try {
        problem = (await res.json()) as Problem;
      } catch {
        problem = null;
      }
      throw new ApiError(res.status, problem?.code ?? `http_${res.status}`, problem, problem?.trace_id ?? traceId);
    }
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  return {
    get: (p, o) => call("GET", p, o),
    post: (p, o) => call("POST", p, o),
    patch: (p, o) => call("PATCH", p, o),
  };
}
