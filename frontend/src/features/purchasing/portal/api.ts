/** Typed client of the supplier portal API (`/api/portal/v1/supplier/*`); the party scope comes from the token only. */
import type { ApiClient } from "@lib/api/client";

export interface Qty {
  value: string;
  uom: string;
}
export interface Money {
  amount: string;
  currency: string;
}
export interface ItemsPage<T> {
  items: T[];
  next_cursor: string | null;
}

export interface SupplierRfq {
  id: string;
  number: string;
  status: string;
  rfq_status: "OPEN" | "CLOSED";
  deadline: string;
  currency: string;
  lines: Array<{ line_no: number; item_id: string | null; description: string; quantity: Qty }>;
}
export interface SupplierQuote {
  id: string;
  number: string;
  status: string;
  currency: string;
  lead_time_days: number;
  valid_until: string | null;
  lines: Array<{ line_no: number; quantity: Qty; unit_price: Money; discount_pct: string }>;
}
export interface SupplierOrder {
  id: string;
  number: string;
  status: string;
  total: Money;
  acknowledged_at: string | null;
  expected_date: string | null;
  lines: Array<{ line_no: number; description: string; quantity: Qty; unit_price: Money; received_qty: string }>;
  answer: { accepted: boolean; comment: string } | null;
}
export interface SupplierShipment {
  id: string;
  number: string;
  status: string;
  purchase_order_id: string;
  expected_date: string;
  tracking: string;
  lines: Array<{ line_no: number; quantity: string }>;
}
export interface SupplierInvoice {
  id: string;
  number: string;
  status: string;
  processing_status: string;
}
export interface SupplierPayment {
  id: string;
  status: string;
  document: { type: string; id: string; number: string };
  amount: Money;
  paid_on: string | null;
  vouchers: Array<{ number: string; state: string; amount: Money; posted_on: string | null }>;
}
export interface SupplierProfile {
  id: string;
  legal_name: string;
  nit: string;
  bank_account: string | null;
  contacts: Record<string, string>;
  pending_change: boolean;
}
export interface SupplierDocument {
  id: string;
  document_type: string;
  file_name: string;
  expires_on: string | null;
  review_status: string;
}

const BASE = "/api/portal/v1/supplier";

export function supplierPortalApi(api: ApiClient) {
  const enc = encodeURIComponent;
  return {
    rfqs: (signal?: AbortSignal) => api.get<ItemsPage<SupplierRfq>>(`${BASE}/rfqs`, { signal }),
    quotes: (rfqId: string, signal?: AbortSignal) => api.get<ItemsPage<SupplierQuote>>(`${BASE}/rfqs/${enc(rfqId)}/quotes`, { signal }),
    submitQuote: (rfqId: string, body: { currency: string; lead_time_days: number; lines: unknown[] }) =>
      api.post<SupplierQuote>(`${BASE}/rfqs/${enc(rfqId)}/quotes`, { body }),
    orders: (signal?: AbortSignal) => api.get<ItemsPage<SupplierOrder>>(`${BASE}/purchase-orders`, { signal }),
    answerOrder: (id: string, accepted: boolean, comment: string) =>
      api.post<SupplierOrder>(`${BASE}/purchase-orders/${enc(id)}:acknowledge`, { body: { comment, parameters: { accepted } } }),
    shipments: (signal?: AbortSignal) => api.get<ItemsPage<SupplierShipment>>(`${BASE}/shipments`, { signal }),
    announce: (body: { purchase_order_id: string; expected_date: string; tracking: string; lines: Array<{ line_no: number; quantity: string }> }) =>
      api.post<SupplierShipment>(`${BASE}/shipments`, { body }),
    invoices: (signal?: AbortSignal) => api.get<ItemsPage<SupplierInvoice>>(`${BASE}/invoices`, { signal }),
    uploadInvoice: (number: string, xmlBase64: string) => api.post<SupplierInvoice>(`${BASE}/invoices`, { body: { number, xml_file: xmlBase64 } }),
    payments: (signal?: AbortSignal) => api.get<ItemsPage<SupplierPayment>>(`${BASE}/payments`, { signal }),
    profile: (signal?: AbortSignal) => api.get<SupplierProfile>(`${BASE}/profile`, { signal }),
    requestChange: (parameters: Record<string, unknown>, reason: string) =>
      api.post<{ status: string; data: { required_approvals: number } }>(`${BASE}/profile/change-requests`, { body: { parameters, reason } }),
    documents: (signal?: AbortSignal) => api.get<ItemsPage<SupplierDocument>>(`${BASE}/documents`, { signal }),
  };
}

export type SupplierPortalApi = ReturnType<typeof supplierPortalApi>;
