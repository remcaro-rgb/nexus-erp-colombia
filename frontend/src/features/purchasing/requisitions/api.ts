// Typed calls to the F-INV-REQ API (contracts/openapi/modules/purchasing.yaml: internal-requisitions, consolidations;
// owner P11, part P11b). The UI uses only the public API; every state change sends an Idempotency-Key (ApiClient) and
// actions use the contract ActionRequest body. The server authorizes everything (the UI is not a security boundary).
import type { ApiClient } from "@lib/api/client";

export interface Qty {
  value: string;
  uom: string;
}
export interface Money {
  amount: string;
  currency: string;
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export type RequisitionStatus =
  | "DRAFT"
  | "SUBMITTED"
  | "APPROVED"
  | "REJECTED"
  | "IN_PROGRESS"
  | "FULFILLED"
  | "PARTIALLY_FULFILLED_CLOSED"
  | "CANCELLED";
export type LineStatus = "OPEN" | "RESERVED" | "IN_TRANSFER" | "TO_PROCURE" | "IN_PURCHASE" | "FULFILLED" | "CANCELLED";

export interface LineageEntry {
  kind: "transfer" | "consolidation" | "purchase_order" | "buyer_task";
  id: string;
  number?: string;
  qty: string;
  reason_code?: string;
}

export interface RequisitionLineDto {
  id: string;
  line_no: number;
  item_id: string;
  description: string;
  quantity: Qty;
  base_qty: string;
  line_status: LineStatus;
  qty_transfer: string;
  qty_procure: string;
  qty_cancelled: string;
  fulfilled_qty: string;
  needed_by: string;
  lineage: LineageEntry[];
}

export interface RequisitionDto {
  id: string;
  number: string;
  document_date: string;
  status: string;
  requisition_status: RequisitionStatus;
  requesting_warehouse_id: string;
  needed_by: string;
  priority: "LOW" | "NORMAL" | "HIGH" | "URGENT";
  notes: string;
  requester_id: string;
  lines: RequisitionLineDto[];
  version: number;
}

export interface NewRequisition {
  document_date: string;
  requesting_warehouse_id: string;
  needed_by: string;
  priority: RequisitionDto["priority"];
  notes?: string;
  lines: Array<{ line_no: number; item_id: string; quantity: Qty; description?: string }>;
}

export interface SourcingLinePlan {
  line_no: number;
  item_id: string;
  requested: string;
  transfer: Array<{ from_warehouse_id: string; from_warehouse_code: string; qty: string }>;
  qty_transfer: string;
  qty_purchase: string;
  source: "TRANSFER" | "PURCHASE" | "MIXED";
}

export interface SourcingResult {
  status: string;
  data: { status: string; lines: SourcingLinePlan[] | Array<Record<string, unknown>>; policy_ref: string };
}

export interface ConsolidationSource {
  line_id: string;
  requisition_id: string;
  requisition_number: string;
  line_no: number;
  needed_by: string;
  qty: string;
}

export interface ConsolidationPreviewLine {
  item_id: string;
  needed_week: string;
  delivery_warehouse_id: string;
  quantity: string;
  sources: ConsolidationSource[];
  action: "DRAFT_PO" | "BUYER_TASK";
  reason_code: string | null;
  estimated_total: string | null;
}

export interface ConsolidationRunResult {
  id: string;
  status: string;
  dry_run: boolean;
  lines: ConsolidationPreviewLine[];
  result_links: Array<{ rel: string; href: string }>;
  warnings: Array<{ code: string; message: string }>;
}

const BASE = "/api/v1/purchasing";

export class RequisitionsApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  list(params?: Record<string, string>, signal?: AbortSignal): Promise<Page<RequisitionDto>> {
    return this.api.get(`${BASE}/internal-requisitions`, { headers: this.headers(), params, signal });
  }
  create(body: NewRequisition): Promise<RequisitionDto> {
    return this.api.post(`${BASE}/internal-requisitions`, { headers: this.headers(), body });
  }
  action(id: string, verb: "submit" | "approve" | "reject" | "cancel", reason = ""): Promise<RequisitionDto> {
    return this.api.post(`${BASE}/internal-requisitions/${encodeURIComponent(id)}:${verb}`, {
      headers: this.headers(),
      body: reason ? { reason } : {},
    });
  }
  source(id: string, mode: "simulate" | "apply"): Promise<SourcingResult> {
    return this.api.post(`${BASE}/internal-requisitions/${encodeURIComponent(id)}:source`, {
      headers: this.headers(),
      params: { mode },
      body: {},
    });
  }
  consolidate(dryRun: boolean): Promise<ConsolidationRunResult> {
    return this.api.post(`${BASE}/consolidations:run`, { headers: this.headers(), params: { dry_run: dryRun }, body: {} });
  }
}
