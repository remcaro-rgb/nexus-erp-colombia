// Typed calls to the stock-issue API (contracts/openapi/modules/inventory.yaml, F-INV-ISSUE).
// The UI uses only the public API; every state change carries an Idempotency-Key (ApiClient) and the company header.
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export type IssueStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "POSTED" | "REVERSED" | "CANCELLED";
export type IssueAction = "submit" | "approve" | "post" | "reverse" | "simulate";
export type DimensionKey = "cost_center" | "business_unit" | "aux_abierto" | "item_contable";

export interface DimensionSlots {
  cost_center_id?: string | null;
  business_unit_id?: string | null;
  aux_abierto_id?: string | null;
  item_dimension_id?: string | null;
}

export interface IssueConceptDto {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  kind: string;
  account_role: string;
  requires_dimension: boolean;
  required_dims: DimensionKey[];
  requires_reason: boolean;
  reason_codes: string[];
  requires_approval: boolean;
  approval_threshold_value?: Money | null;
  version: number;
}

export interface IssueConceptInput {
  code: string;
  name: string;
  account_role: string;
  kind?: string;
  requires_dimension?: boolean;
  requires_reason?: boolean;
  requires_approval?: boolean;
  required_dims?: DimensionKey[];
  reason_codes?: string[];
  approval_threshold_value?: Money | null;
}

export interface IssueLineDto {
  line_no: number;
  item_id: string;
  description: string;
  quantity: { value: string; uom: string };
  qty_packages: string | null;
  presentation_id: string | null;
  lot_id: string | null;
  location_id: string | null;
  dimensions: DimensionSlots;
  /** confidential: absent for roles without cost visibility */
  unit_price?: Money | null;
  amount?: Money | null;
}

export interface IssueDto {
  id: string;
  number: string;
  document_date: string;
  posting_date: string | null;
  status: IssueStatus;
  /** confidential: absent for roles without cost visibility */
  total?: Money | null;
  concept_id: string;
  concept_code: string;
  warehouse_id: string;
  reason: string | null;
  dimensions: DimensionSlots;
  lines: IssueLineDto[];
  approval_id: string | null;
  journal_entry_id: string | null;
  reversal_entry_id: string | null;
  version: number;
}

export interface IssueLineInput {
  item_id: string;
  quantity?: string;
  qty_packages?: string;
  presentation_id?: string | null;
  location_id?: string | null;
  lot_id?: string | null;
  dimensions?: DimensionSlots;
}

export interface IssueInput {
  concept_id: string;
  warehouse_id: string;
  document_date?: string;
  reason?: string | null;
  notes?: string;
  dimensions?: DimensionSlots;
  lines: IssueLineInput[];
}

export interface SimulationDto {
  status: string;
  data?: {
    total: Money;
    lines: Array<{ line_no: number; item_id: string; unit_cost: string; value: Money }>;
    journal: Array<{ account_code: string; account_name: string; debit: string; credit: string }>;
  };
}

const BASE = "/api/v1/inventory";

export class IssuesApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  concepts(signal?: AbortSignal): Promise<Page<IssueConceptDto>> {
    return this.api.get(`${BASE}/issue-concepts`, { params: { limit: 200, sort: "code" }, headers: this.headers(), signal });
  }

  createConcept(body: IssueConceptInput): Promise<IssueConceptDto> {
    return this.api.post(`${BASE}/issue-concepts`, { body, headers: this.headers() });
  }

  updateConcept(id: string, body: Partial<IssueConceptInput>, etag: string): Promise<IssueConceptDto> {
    return this.api.patch(`${BASE}/issue-concepts/${id}`, { body, ifMatch: etag, headers: this.headers() });
  }

  issues(params: { status?: string } = {}, signal?: AbortSignal): Promise<Page<IssueDto>> {
    return this.api.get(`${BASE}/issues`, { params: { limit: 100, sort: "-document_date", ...params }, headers: this.headers(), signal });
  }

  create(body: IssueInput): Promise<IssueDto> {
    return this.api.post(`${BASE}/issues`, { body, headers: this.headers() });
  }

  action(id: string, action: Exclude<IssueAction, "simulate">, body: { reason?: string; parameters?: Record<string, unknown> } = {}): Promise<IssueDto> {
    return this.api.post(`${BASE}/issues/${id}:${action}`, { body, headers: this.headers() });
  }

  simulate(id: string): Promise<SimulationDto> {
    return this.api.post(`${BASE}/issues/${id}:simulate`, { body: {}, headers: this.headers() });
  }
}
