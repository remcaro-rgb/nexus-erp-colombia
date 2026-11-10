/** Typed client for `/api/v1/einvoice/*` through the shell's ApiClient (public API only, X-Company-Id scoped). */
import type { ApiClient } from "@lib/api/client";
import type { ContingencyPanel, ContractReport, FiscalDocument, NumberingRange, TechnologyProvider } from "./model";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ActionResult<D = Record<string, unknown>> {
  status: string;
  resource?: { type: string; id: string };
  data?: D;
}

/** GET /api/v1/kpis/facturacion/panels/contingencia */
export interface ContingencyPanelResponse {
  state: string;
  contingency?: ContingencyPanel;
}

const BASE = "/api/v1/einvoice";

export function einvoiceApi(api: ApiClient, companyId: string) {
  const headers = { "X-Company-Id": companyId };
  return {
    documents: (params: Record<string, string | undefined> = {}, signal?: AbortSignal) =>
      api.get<Page<FiscalDocument>>(`${BASE}/documents`, { params: { limit: 200, ...params }, headers, signal }),
    retry: (id: string, reason: string) => api.post<FiscalDocument>(`${BASE}/documents/${id}:retry`, { body: { reason }, headers }),
    resendEmail: (id: string) => api.post<FiscalDocument>(`${BASE}/documents/${id}:resend-email`, { body: {}, headers }),
    events: (id: string) => api.get<Page<Record<string, unknown>>>(`${BASE}/documents/${id}/events`, { headers }),
    xmlUrl: (id: string) => `${BASE}/documents/${id}/xml`,
    ranges: (signal?: AbortSignal) => api.get<Page<NumberingRange>>(`${BASE}/numbering-ranges`, { headers, signal }),
    createRange: (body: Omit<NumberingRange, "id" | "status" | "version"> & { technical_key_ref?: string }) =>
      api.post<NumberingRange>(`${BASE}/numbering-ranges`, { body, headers }),
    declareContingency: (reason: string) =>
      api.post(`${BASE}/contingency:declare`, { body: { reason, parameters: { kind: "ISSUER_OUTAGE" } }, headers }),
    endContingency: () => api.post(`${BASE}/contingency:end`, { body: {}, headers }),
    providers: (signal?: AbortSignal) => api.get<Page<TechnologyProvider>>(`${BASE}/providers`, { headers, signal }),
    createProvider: (body: Omit<TechnologyProvider, "id" | "status" | "version"> & { credentials_ref?: string }) =>
      api.post<TechnologyProvider>(`${BASE}/providers`, { body, headers }),
    testConnection: (id: string) =>
      api.post<ActionResult<{ config_state: string; report: ContractReport; label: string }>>(`${BASE}/providers/${id}:test-connection`, {
        body: {},
        headers,
      }),
    exportUrl: () => `${BASE}/providers:export`,
    contingency: (signal?: AbortSignal) =>
      api.get<ContingencyPanelResponse>("/api/v1/kpis/facturacion/panels/contingencia", { params: { company_id: companyId }, headers, signal }),
  };
}

export type EinvoiceApi = ReturnType<typeof einvoiceApi>;
