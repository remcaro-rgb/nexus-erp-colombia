// Typed calls to the treasury API (contracts/openapi/modules/treasury.yaml, F-TRE-RC / F-TRE-CE). The UI uses only the
// public API; every state change sends an Idempotency-Key (ApiClient) and actions use the contract ActionRequest body.
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export type VoucherStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "POSTED" | "REVERSED" | "CANCELLED";
export type VoucherKind = "receipts" | "disbursements";
export type PaymentMethod = "CASH" | "TRANSFER" | "CHECK" | "CARD" | "OTHER";

export interface DimensionSlots {
  cost_center_id?: string | null;
  business_unit_id?: string | null;
  aux_abierto_id?: string | null;
  item_dimension_id?: string | null;
}
export interface VoucherLine {
  line_no: number;
  description?: string;
  quantity?: { value: string; uom: string };
  unit_price?: Money | null;
  amount?: Money | null;
  tax_code?: string | null;
  dimensions?: DimensionSlots;
  concept_id?: string | null;
  open_item_id?: string | null;
  applied_amount?: Money | null;
}
export interface VoucherDto {
  id: string;
  number: string;
  document_date: string;
  posting_date: string | null;
  status: VoucherStatus;
  party?: { id: string; kind?: string };
  currency: string;
  total: Money;
  notes: string;
  fund_id: string;
  payment_method: PaymentMethod;
  reference: string | null;
  withholdings: { total: Money; lines: Array<{ line_no: number; tax_code: string; base: string; rate: string | null; amount: string }> };
  lines: VoucherLine[];
  version: number;
}
export interface VoucherInput {
  document_date: string;
  fund_id: string;
  payment_method: PaymentMethod;
  currency?: string;
  party?: { id: string; kind: string };
  reference?: string | null;
  notes?: string;
  lines: VoucherLine[];
}
export interface FundDto {
  id: string;
  code: string;
  name: string;
  fund_type: "BANK" | "CASH";
  allow_overdraft: boolean;
  status: string;
}
export interface ConceptDto {
  id: string;
  code: string;
  name: string;
  direction: "IN" | "OUT" | "BOTH";
  account_role: string;
  requires_dimension: boolean;
  status: string;
}
export interface BatchDto {
  id: string;
  number: string;
  status: VoucherStatus;
  payment_date: string;
  total: Money;
  fund_id: string;
  lines: Array<{ line_no: number; description: string; amount: Money; tax_code: string | null }>;
}
export interface SimulationDto {
  persisted: false;
  journal_entries: Array<{ journal: string; posting_date: string; lines: Array<{ account: string; debit: Money; credit: Money }> }>;
  warnings?: Array<{ code: string; message: string }>;
}
export interface OperationDto {
  id: string;
  status: string;
  file?: { label: string; name: string; sha256: string; content: string };
}
export type VoucherAction = "submit" | "approve" | "reject" | "post" | "reverse";

export class TreasuryApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }
  private get<T>(path: string, params?: Record<string, string>): Promise<T> {
    return this.api.get<T>(`/api/v1/treasury/${path}`, { headers: this.headers(), params });
  }
  private post<T>(path: string, body: unknown = {}): Promise<T> {
    return this.api.post<T>(`/api/v1/treasury/${path}`, { headers: this.headers(), body });
  }

  vouchers(kind: VoucherKind, status?: VoucherStatus): Promise<Page<VoucherDto>> {
    return this.get(kind, status ? { status } : undefined);
  }
  createVoucher(kind: VoucherKind, input: VoucherInput): Promise<VoucherDto> {
    return this.post(kind, input);
  }
  simulate(kind: VoucherKind, id: string): Promise<SimulationDto> {
    return this.post(`${kind}/${id}:simulate`);
  }
  act(kind: VoucherKind, id: string, action: VoucherAction, reason = ""): Promise<VoucherDto> {
    return this.post(`${kind}/${id}:${action}`, reason ? { reason } : {});
  }
  funds(): Promise<Page<FundDto>> {
    return this.get("funds", { status: "ACTIVE" });
  }
  concepts(): Promise<Page<ConceptDto>> {
    return this.get("concepts");
  }
  batches(): Promise<Page<BatchDto>> {
    return this.get("payment-batches");
  }
  batchAction(id: string, action: "submit" | "approve" | "execute" | "generate-file" | "mark-sent"): Promise<BatchDto | OperationDto> {
    return this.post(`payment-batches/${id}:${action}`);
  }
}
