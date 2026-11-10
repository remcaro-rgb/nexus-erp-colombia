// Typed calls to the transformation API (contracts/openapi/modules/inventory.yaml § transformations,
// portioning-templates, repack-rules; F-INV-XFORM). Every state change carries an Idempotency-Key and the company header.
import type { ApiClient } from "@lib/api/client";
import type { Page } from "../api";

export type TransformationType = "PRESENTATION" | "PORTIONING" | "MANUAL" | "PRODUCTION";
export type TransformationStatus = "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "POSTED" | "REVERSED" | "CANCELLED";
export type LineRole = "INPUT" | "OUTPUT" | "WASTE";
export type Money = { amount: string; currency: string };

export interface TransformationLineDto {
  line_no: number;
  role: LineRole;
  item_id: string | null;
  item_code: string | null;
  quantity: { value: string; uom: string };
  actual_weight: { value: string; uom: string } | null;
  cost_share: string | null;
  is_main: boolean;
  is_byproduct: boolean;
  abnormal: boolean;
  value?: Money | null;
}

export interface TransformationDto {
  id: string;
  number: string;
  transformation_type: TransformationType;
  status: TransformationStatus;
  warehouse_id: string;
  template_id: string | null;
  bom_id: string | null;
  document_date: string;
  inputs_reserved: boolean;
  approval_required: boolean;
  approval_reasons: string | null;
  value_in?: Money | null;
  value_out?: Money | null;
  waste_expensed?: Money | null;
  journal_entry_id: string | null;
  lines: TransformationLineDto[];
}

export interface TransformationLineInput {
  line_no: number;
  role: LineRole;
  item_id?: string;
  quantity?: string;
  qty_packages?: string;
  presentation_id?: string;
  actual_weight?: string;
  cost_share?: string;
  is_main?: boolean;
  abnormal?: boolean;
}

export interface TransformationInput {
  transformation_type: TransformationType;
  warehouse_id: string;
  document_date?: string;
  template_id?: string;
  bom_id?: string;
  planned_qty?: string;
  additional_costs?: Money;
  reason?: string;
  lines: TransformationLineInput[];
}

export interface SimulationDto {
  balanced: boolean;
  approval_required: boolean;
  approval_reasons: string[];
  yield_checks: Array<{ expected: string; actual: string; tolerance: string; within: boolean }>;
  allocation_basis: string | null;
  qty_in: string;
  qty_out: string;
  value_in?: Money;
  value_out?: Money;
  waste_expensed?: Money;
  journal?: Array<{ account_role: string; side: "DEBIT" | "CREDIT"; amount: Money }>;
}

export interface TemplateDto {
  id: string;
  code: string;
  name: string;
  status: string;
  input_item_id: string;
  tolerance_percent: string;
  waste_percent: string;
  outputs: { lines: Array<{ item_id: string; item_code: string; expected_yield_percent: string; cost_share_percent: string | null; is_main: boolean }> };
}

export interface TemplateInput {
  code: string;
  name: string;
  input_item_id: string;
  tolerance_percent: string;
  waste_percent: string;
  outputs: { lines: Array<{ item_id: string; expected_yield_percent: string; cost_share_percent?: string; is_main?: boolean }> };
}

export type TransformationAction = "reserve" | "submit" | "approve" | "reject" | "cancel" | "post" | "reverse";

const BASE = "/api/v1/inventory";

export class TransformationApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  list(type: TransformationType, signal?: AbortSignal): Promise<Page<TransformationDto>> {
    return this.api.get(`${BASE}/transformations`, { params: { limit: 100, sort: "-document_date", transformation_type: type }, headers: this.headers(), signal });
  }

  create(body: TransformationInput): Promise<TransformationDto> {
    return this.api.post(`${BASE}/transformations`, { body, headers: this.headers() });
  }

  action(id: string, action: TransformationAction, reason?: string): Promise<TransformationDto> {
    return this.api.post(`${BASE}/transformations/${id}:${action}`, { body: reason ? { reason } : {}, headers: this.headers() });
  }

  simulate(id: string): Promise<SimulationDto> {
    return this.api.post(`${BASE}/transformations/${id}:simulate`, { body: {}, headers: this.headers() });
  }

  templates(signal?: AbortSignal): Promise<Page<TemplateDto>> {
    return this.api.get(`${BASE}/portioning-templates`, { params: { limit: 200, sort: "code" }, headers: this.headers(), signal });
  }

  createTemplate(body: TemplateInput): Promise<TemplateDto> {
    return this.api.post(`${BASE}/portioning-templates`, { body, headers: this.headers() });
  }
}
