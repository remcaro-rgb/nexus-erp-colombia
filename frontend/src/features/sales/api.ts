// Typed calls to the sales API (contracts/openapi/modules/sales.yaml, P10a core + CCR P10a-1 additive fields). The UI uses
// only the public API; every state change sends an Idempotency-Key (ApiClient) and actions use the ActionRequest body.
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
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

export type PriceSource = "OVERRIDE" | "FORMULA" | "LIST" | "BASE";
export type OverrideStatus = "NONE" | "WITHIN_LIMIT" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED";

/** Price snapshot of a line as the principal may read it (cost input and trace only with the cost role). */
export interface PriceSnapshot {
  source?: PriceSource;
  precedence?: PriceSource[];
  resolved_source?: PriceSource | null;
  resolved_price?: string | null;
  unit_price?: string;
  currency?: string;
  fx_rate?: string;
  rate_date?: string;
  rounding?: string;
  quantity?: string;
  detail?: {
    price_list_code?: string;
    min_qty?: string;
    binding?: string;
    formula_code?: string;
    formula_version?: number;
    inputs?: Record<string, string>;
    trace?: Array<{ expr: string; value: string }>;
    result_cop?: string;
  };
  override?: {
    reason?: string;
    override_price?: string | null;
    effective_discount_pct?: string;
    limit_pct?: string;
    approval?: OverrideStatus;
  } | null;
  result_before_tax?: string;
  hash?: string;
}

export interface SalesLineDto {
  id: string;
  line_no: number;
  item_id: string | null;
  description: string;
  quantity: { value: string; uom: string };
  unit_price: Money;
  discount_pct: string;
  amount: Money;
  tax_code: string | null;
  price_source: PriceSource | null;
  override_status: OverrideStatus;
  price_snapshot: PriceSnapshot;
  cost_value?: Money | null;
  margin?: Money | null;
}

export interface SalesDocumentDto {
  id: string;
  number: string;
  document_date: string;
  status: string;
  document_status: string;
  party?: PartyRef;
  currency: string;
  subtotal: Money;
  tax_total: Money;
  total: Money;
  due_date?: string | null;
  valid_until?: string | null;
  fiscal_status?: string | null;
  lines: SalesLineDto[];
  version: number;
  reprice_diff?: RepriceDiff[];
}

export interface RepriceDiff {
  line_no: number;
  item_id: string;
  old: { unit_price: string; source: PriceSource | null; amount: string };
  new: { unit_price: string; source: PriceSource; amount: string };
  delta_amount: string;
  changed: boolean;
}

export interface PartyDto {
  id: string;
  code: string;
  legal_name?: string;
  nit?: string;
  dv?: string;
  person_type: string;
  roles: string[];
  customer_group: string;
  payment_terms_days: number;
  credit_limit?: Money | null;
  status: string;
}

export interface PriceFormulaDto {
  id: string;
  code: string;
  name: string;
  formula_code: string;
  formula_id: string;
  scope: Record<string, string | string[]>;
  priority: number;
  valid_from: string;
  valid_to: string | null;
  status: string;
}

export interface QuotedPrice {
  item_id: string;
  quantity: string;
  unit_price: Money;
  source: PriceSource;
  override_status: OverrideStatus;
  discount_pct: string;
  explain: PriceSnapshot;
}

export interface OpenItemDto {
  id: string;
  party?: PartyRef;
  document: { type: string; id: string; number: string };
  document_date: string;
  due_date: string;
  currency: string;
  amount?: Money;
  balance?: Money;
  status: "OPEN" | "PARTIAL" | "PAID";
}

export type AgingBucket = "current" | "1_30" | "31_60" | "61_90" | "over_90";
export interface AgingDto {
  as_of: string;
  currency: string;
  buckets: AgingBucket[];
  parties: Array<{ party: PartyRef; name: string; buckets: Record<AgingBucket, string>; total: string }>;
  totals: Record<AgingBucket, string>;
  total: string;
}

export class SalesApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  private get<T>(path: string, params?: Record<string, string>): Promise<T> {
    return this.api.get<T>(`/api/v1/sales/${path}`, { headers: this.headers(), params });
  }

  private action<T>(path: string, body: { reason?: string; parameters?: Record<string, unknown> } = {}): Promise<T> {
    return this.api.post<T>(`/api/v1/sales/${path}`, { headers: this.headers(), body });
  }

  parties(params?: Record<string, string>): Promise<Page<PartyDto>> {
    return this.get("parties", params);
  }
  priceFormulas(): Promise<Page<PriceFormulaDto>> {
    return this.get("price-formulas");
  }
  quotePrices(party: string | null, items: Array<{ item_id: string; quantity: string }>, date?: string): Promise<{ data: { items: QuotedPrice[] } }> {
    return this.action("pricing:quote", { parameters: { ...(party ? { party } : {}), items, ...(date ? { date } : {}) } });
  }
  quotes(status?: string): Promise<Page<SalesDocumentDto>> {
    return this.get("quotes", status ? { status } : undefined);
  }
  orders(status?: string): Promise<Page<SalesDocumentDto>> {
    return this.get("orders", status ? { status } : undefined);
  }
  invoices(status?: string): Promise<Page<SalesDocumentDto>> {
    return this.get("invoices", status ? { status } : undefined);
  }
  reprice(kind: "quotes" | "invoices", id: string, apply: boolean): Promise<SalesDocumentDto> {
    return this.action(`${kind}/${id}:reprice`, { parameters: { apply } });
  }
  orderAction(id: string, action: "confirm" | "cancel" | "to-invoice", reason = ""): Promise<SalesDocumentDto> {
    return this.action(`orders/${id}:${action}`, reason ? { reason } : {});
  }
  postInvoice(id: string): Promise<SalesDocumentDto> {
    return this.action(`invoices/${id}:post`);
  }
  openItems(params?: Record<string, string>): Promise<Page<OpenItemDto>> {
    return this.get("ar/open-items", params);
  }
  aging(asOf?: string): Promise<AgingDto> {
    return this.get("ar/aging", asOf ? { as_of: asOf } : undefined);
  }
}
