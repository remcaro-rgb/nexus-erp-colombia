// Typed calls to the purchasing API (contracts/openapi/modules/purchasing.yaml, F-PUR-CYCLE). The UI uses only the public
// API; every state change sends an Idempotency-Key (ApiClient) and actions use the contract ActionRequest body.
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}
export interface Qty {
  value: string;
  uom: string;
}
export interface PartyRef {
  id: string;
  kind: string;
  name?: string;
  tax_id?: string;
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export type RfqStatus = "DRAFT" | "SENT" | "CLOSED" | "EVALUATED" | "AWARD_PENDING_APPROVAL" | "AWARDED" | "PO_CREATED" | "CANCELLED";
export type QuoteStatus = "RECEIVED" | "LATE" | "WITHDRAWN" | "DISQUALIFIED" | "AWARDED";

export interface RfqLineDto {
  id: string;
  line_no: number;
  item_id: string | null;
  description: string;
  quantity: Qty;
}
export interface AwardDto {
  id: string;
  status: "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  recommended_quote_id: string | null;
  differs_from_recommendation: boolean;
  reason: string | null;
  proposed_by: string;
}
export interface RfqDto {
  id: string;
  number: string;
  document_date: string;
  deadline: string;
  status: RfqStatus;
  currency: string;
  invited_vendors: string[];
  awarded_quote_id: string | null;
  award_reason: string | null;
  award: AwardDto | null;
  lines: RfqLineDto[];
  version: number;
  warnings?: string[];
}
export interface QuoteDto {
  id: string;
  number: string;
  rfq_id: string;
  party?: PartyRef;
  currency: string;
  received_at: string;
  lead_time_days: number;
  payment_terms_days: number;
  quote_status: QuoteStatus;
  landed_total?: Money;
  total: Money;
  warnings?: string[];
}
export type Criterion = "price" | "lead_time" | "rating" | "payment_terms";
export interface ComparedQuote {
  quote_id: string;
  vendor_id: string;
  vendor_code: string;
  vendor_name: string;
  received_at: string;
  currency: string;
  fx_rate: string;
  lead_time_days: number;
  payment_terms_days: number;
  rating: string;
  comparable_total_cop: string;
  lines: Array<{ line_no: number; offered_qty: string; offered_uom: string; base_qty: string; unit_landed_cop: string; comparable_cop: string; short_quantity: boolean }>;
  rank?: number;
  factors?: Record<Criterion, string>;
  points?: Record<Criterion, string>;
  score?: string;
  reason?: string;
}
export interface ComparisonDto {
  id: string;
  rfq_id: string;
  sequence: number;
  created_at: string;
  weights: Record<Criterion, string>;
  ranked: ComparedQuote[];
  excluded: ComparedQuote[];
  recommended_quote_id: string | null;
  tie_rule: string | null;
  explanation: string;
  warnings: string[];
}
export interface OrderDto {
  id: string;
  number: string;
  document_date: string;
  status: string;
  party?: PartyRef;
  currency: string;
  total: Money;
  rfq: { id: string; number: string | null } | null;
  lines: Array<{ id: string; line_no: number; description: string; quantity: Qty; unit_price: Money; received_qty: string; invoiced_qty: string }>;
  version: number;
}
export interface ReceiptDto {
  id: string;
  number: string;
  document_date: string;
  status: string;
  receipt_status: "DRAFT" | "VALIDATED" | "CANCELLED";
  order: { id: string; number: string };
  warehouse_id: string;
  total: Money;
}

export class PurchasingApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  private get<T>(path: string, params?: Record<string, string>): Promise<T> {
    return this.api.get<T>(`/api/v1/purchasing/${path}`, { headers: this.headers(), params });
  }

  private action<T>(path: string, body: { reason?: string; parameters?: Record<string, unknown> } = {}): Promise<T> {
    return this.api.post<T>(`/api/v1/purchasing/${path}`, { headers: this.headers(), body });
  }

  rfqs(status?: RfqStatus): Promise<Page<RfqDto>> {
    return this.get("rfqs", status ? { status } : undefined);
  }
  rfq(id: string): Promise<RfqDto> {
    return this.get(`rfqs/${id}`);
  }
  quotes(rfqId: string): Promise<Page<QuoteDto>> {
    return this.get(`rfqs/${rfqId}/quotes`);
  }
  comparison(rfqId: string): Promise<ComparisonDto> {
    return this.get(`rfqs/${rfqId}/comparison`);
  }
  rfqAction(rfqId: string, action: "send" | "close" | "evaluate"): Promise<RfqDto> {
    return this.action(`rfqs/${rfqId}:${action}`);
  }
  reopen(rfqId: string, reason: string, deadline: string): Promise<RfqDto> {
    return this.action(`rfqs/${rfqId}:reopen`, { reason, parameters: { deadline } });
  }
  award(rfqId: string, quoteId: string, reason: string): Promise<RfqDto> {
    return this.action(`rfqs/${rfqId}:award`, { reason: reason || undefined, parameters: { quote_id: quoteId } });
  }
  approveAward(rfqId: string, approve: boolean, reason: string): Promise<RfqDto> {
    return this.action(`rfqs/${rfqId}:approve-award`, { reason, parameters: { approve } });
  }
  orders(status?: string): Promise<Page<OrderDto>> {
    return this.get("orders", status ? { status } : undefined);
  }
  orderAction(id: string, action: "submit" | "approve" | "send" | "close" | "cancel", reason = ""): Promise<OrderDto> {
    return this.action(`orders/${id}:${action}`, reason ? { reason } : {});
  }
  receipts(): Promise<Page<ReceiptDto>> {
    return this.get("receipts");
  }
  validateReceipt(id: string): Promise<ReceiptDto> {
    return this.action(`receipts/${id}:validate`);
  }
}
