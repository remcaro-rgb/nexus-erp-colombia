/** Typed client for `/api/v1/pos/*` through the shell's ApiClient (public API only, X-Company-Id scoped). */
import type { ApiClient } from "@lib/api/client";
import type { CatalogItem, CommittedTicket } from "./model";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface PosSession {
  id: string;
  number: string;
  status: string;
  terminal_id: string;
  cashier_id: string;
  opened_at: string;
  closed_at: string | null;
  difference?: { amount: string; currency: string } | null;
}

export interface PosTerminal {
  id: string;
  code: string;
  name: string;
  status: string;
  config_id: string;
}

export interface CheckoutResult {
  status: string;
  resource: { type: string; id: string; number: string };
  data: CommittedTicket;
}

const BASE = "/api/v1/pos";

export function posApi(api: ApiClient, companyId: string) {
  const headers = { "X-Company-Id": companyId };
  return {
    terminals: (signal?: AbortSignal) => api.get<Page<PosTerminal>>(`${BASE}/terminals`, { headers, signal }),
    sessions: (params: Record<string, string | undefined> = {}, signal?: AbortSignal) =>
      api.get<Page<PosSession>>(`${BASE}/sessions`, { params: { limit: 200, ...params }, headers, signal }),
    openSession: (terminalId: string, openingCash: string) =>
      api.post<PosSession>(`${BASE}/sessions:open`, { body: { parameters: { terminal_id: terminalId, opening_cash: openingCash } }, headers }),
    closeSession: (id: string, counted: Record<string, string>) =>
      api.post<PosSession>(`${BASE}/sessions/${id}:close`, { body: { parameters: { counted } }, headers }),
    tickets: (params: Record<string, string | undefined> = {}, signal?: AbortSignal) =>
      api.get<Page<CommittedTicket>>(`${BASE}/tickets`, { params: { limit: 200, ...params }, headers, signal }),
    /** The ONLY finalization: Idempotency-Key = the draft's client UUID (a retry never creates a second ticket). */
    checkout: (clientUuid: string, parameters: Record<string, unknown>) =>
      api.post<CheckoutResult>(`${BASE}/tickets:checkout`, { body: { parameters }, headers, idempotencyKey: clientUuid }),
    preview: (parameters: Record<string, unknown>) =>
      api.post<{ status: string; data: { total: string; lines: unknown[] } }>(`${BASE}/tickets:preview`, { body: { parameters }, headers }),
    catalog: (configId?: string, signal?: AbortSignal) =>
      api.get<{ rows: CatalogItem[] }>(`${BASE}/catalog-snapshot`, { params: { config_id: configId }, headers, signal }),
    zReport: (sessionId: string) => api.get<{ rows: unknown[]; totals: Record<string, unknown> }>(`${BASE}/reports/z`, { params: { session_id: sessionId }, headers }),
  };
}

export type PosApi = ReturnType<typeof posApi>;
