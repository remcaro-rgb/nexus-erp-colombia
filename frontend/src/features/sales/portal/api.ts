/** Typed client of the customer portal API (`/api/portal/v1/customer/*`); the party scope comes from the token only. */
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}
export interface ItemsPage<T> {
  items: T[];
  next_cursor: string | null;
}
/** Contract `Customer{Quote,Order,Invoice,CreditNote,Receipt}` (allow-listed fields; never costs or formulas). */
export interface CustomerDocument {
  id: string;
  status: string;
  number: string;
  total: Money | null;
  valid_until: string | null;
  quote_status: string;
  version: number;
}
export interface CustomerOpenItem {
  document: { type: string; id: string; number: string };
  due_date: string | null;
  balance: Money;
}
export interface CustomerPaymentIntent {
  id: string;
  status: string;
  documents: string[];
  amount: Money;
  intent_status: "CREATED" | "SUCCEEDED_SIMULATED" | "FAILED" | "CONFIRMED_BY_TREASURY";
  provider: string;
}
export type RequestType = "QUOTE" | "PROFILE_CHANGE" | "GENERAL";
export interface CustomerRequest {
  id: string;
  number: string;
  status: string;
  request_type: RequestType;
  message: string;
  items: Array<{ item_id: string; quantity: string }>;
  changes: Record<string, string>;
  request_status: string;
  resolution: string;
  quote_id: string | null;
  order_id: string | null;
}
export interface CustomerProfile {
  id: string;
  legal_name: string;
  nit: string;
  email: string | null;
}
export interface StatementRow {
  date: string;
  document_type: string;
  number: string;
  debit: string;
  credit: string;
  balance: string;
}
export interface Statement {
  parameters: { from: string; to: string; currency: string };
  rows: StatementRow[];
  totals: { opening_balance: string; debit: string; credit: string; closing_balance: string; open_balance: string; aging: Record<string, string> };
  warnings: string[];
}
export interface RequestInput {
  request_type: RequestType;
  message: string;
  items?: Array<{ item_id: string; quantity: string }>;
  changes?: Record<string, string>;
}

export const BASE = "/api/portal/v1/customer";

export function customerPortalApi(api: ApiClient) {
  const enc = encodeURIComponent;
  const list = <T,>(path: string) => (signal?: AbortSignal) => api.get<ItemsPage<T>>(`${BASE}/${path}`, { signal });
  return {
    quotes: list<CustomerDocument>("quotes"),
    acceptQuote: (id: string) => api.post<{ status: string; resource: { id: string; number: string } }>(`${BASE}/quotes/${enc(id)}:accept`, { body: {} }),
    orders: list<CustomerDocument>("orders"),
    invoices: list<CustomerDocument>("invoices"),
    creditNotes: list<CustomerDocument>("credit-notes"),
    receipts: list<CustomerDocument>("receipts"),
    openItems: list<CustomerOpenItem>("open-items"),
    intents: list<CustomerPaymentIntent>("payment-intents"),
    createIntent: (documents: string[], amount: Money) => api.post<CustomerPaymentIntent>(`${BASE}/payment-intents`, { body: { documents, amount } }),
    simulate: (id: string, outcome: "success" | "failure") =>
      api.post<CustomerPaymentIntent>(`${BASE}/payment-intents/${enc(id)}:simulate`, { body: { parameters: { outcome } } }),
    requests: list<CustomerRequest>("requests"),
    createRequest: (body: RequestInput) => api.post<CustomerRequest>(`${BASE}/requests`, { body }),
    statement: (from: string, to: string, signal?: AbortSignal) => api.get<Statement>(`${BASE}/statement`, { params: { from, to }, signal }),
    profile: (signal?: AbortSignal) => api.get<CustomerProfile>(`${BASE}/profile`, { signal }),
    invoicePdfUrl: (id: string) => `${BASE}/invoices/${enc(id)}/pdf`,
    invoiceXmlUrl: (id: string) => `${BASE}/invoices/${enc(id)}/xml`,
    notePdfUrl: (id: string) => `${BASE}/credit-notes/${enc(id)}/pdf`,
    noteXmlUrl: (id: string) => `${BASE}/credit-notes/${enc(id)}/xml`,
    statementUrl: (from: string, to: string, format: "csv" | "pdf") => `${BASE}/statement?from=${enc(from)}&to=${enc(to)}&format=${format}`,
  };
}

export type CustomerPortalApi = ReturnType<typeof customerPortalApi>;
