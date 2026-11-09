/** Typed client for `/api/v1/tax/*` (public API only; same permissions as every other channel). */

import type { Parameter } from "./model";

export interface TaxApiContext {
  companyId: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export class TaxApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    detail: string,
  ) {
    super(detail);
  }
}

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

async function request<T>(ctx: TaxApiContext, method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const doFetch = ctx.fetchImpl ?? fetch;
  const init: RequestInit = {
    method,
    signal: ctx.signal,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "X-Company-Id": ctx.companyId,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "Idempotency-Key": newIdempotencyKey() } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  };
  const resp = await doFetch(`/api/v1/tax/${path}`, init);
  const data = resp.status === 204 ? null : await resp.json();
  if (!resp.ok) {
    throw new TaxApiError(resp.status, data?.code ?? "error", data?.detail ?? resp.statusText);
  }
  return data as T;
}

export const taxApi = {
  listParameters: (ctx: TaxApiContext, code?: string) =>
    request<Page<Parameter>>(ctx, "GET", `parameters?limit=500${code ? `&code=${encodeURIComponent(code)}` : ""}`),
  proposeParameter: (ctx: TaxApiContext, parameters: Record<string, unknown>, reason: string) =>
    request<Parameter>(ctx, "POST", "parameters:propose", { reason, parameters }),
  approveParameter: (ctx: TaxApiContext, id: string, reason: string, legalReview: boolean) =>
    request<Parameter>(ctx, "POST", `parameters/${id}:approve`, { reason, parameters: { legal_review: legalReview } }),
  impact: (ctx: TaxApiContext, id: string) =>
    request<{ count: number; affected: Record<string, string>[] }>(ctx, "GET", `parameters/${id}/impact`),
  compute: (ctx: TaxApiContext, parameters: Record<string, unknown>) =>
    request<{ status: string; data: Record<string, unknown>; warnings?: { code: string; detail: string }[] }>(
      ctx,
      "POST",
      "compute",
      { parameters },
    ),
  validateNit: (ctx: TaxApiContext, nit: string) =>
    request<{ status: string; data: { check_digit: number; formatted: string } }>(ctx, "POST", "localization/nit:validate", {
      parameters: { nit },
    }),
};
