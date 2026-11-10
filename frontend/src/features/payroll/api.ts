/** Typed client for `/api/v1/payroll/*` (public API only; the same permissions as every other channel). */

import type { GraphValidation, PayrollConcept, Payslip } from "./model";

export interface PayrollApiContext {
  companyId: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export class PayrollApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(ctx: PayrollApiContext, method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
  const doFetch = ctx.fetchImpl ?? fetch;
  const resp = await doFetch(`/api/v1/payroll/${path}`, {
    method,
    signal: ctx.signal,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "X-Company-Id": ctx.companyId,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "Idempotency-Key": crypto.randomUUID() } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = resp.status === 204 ? null : await resp.json();
  if (!resp.ok) throw new PayrollApiError(resp.status, data?.code ?? "error", data?.detail ?? resp.statusText);
  return data as T;
}

export const payrollApi = {
  listConcepts: (ctx: PayrollApiContext) => request<Page<PayrollConcept>>(ctx, "GET", "concepts?limit=500"),
  createConcept: (ctx: PayrollApiContext, body: Partial<PayrollConcept>) => request<PayrollConcept>(ctx, "POST", "concepts", body),
  updateConcept: (ctx: PayrollApiContext, id: string, etag: string, patch: Partial<PayrollConcept>) =>
    request<PayrollConcept>(ctx, "PATCH", `concepts/${id}`, patch, { "If-Match": etag }),
  validateGraph: (ctx: PayrollApiContext, onDate?: string) =>
    request<GraphValidation>(ctx, "POST", "concepts:validate-graph", onDate ? { parameters: { on: onDate } } : {}),
  simulateRun: (ctx: PayrollApiContext, runId: string) => request<Record<string, unknown>>(ctx, "POST", `runs/${runId}:simulate`, {}),
  getPayslip: (ctx: PayrollApiContext, id: string) => request<Payslip>(ctx, "GET", `payslips/${id}`),
};
