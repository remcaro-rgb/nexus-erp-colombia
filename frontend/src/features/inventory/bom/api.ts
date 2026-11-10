// Typed calls to the recipes / BOM API (contracts/openapi/modules/inventory.yaml § /boms, F-INV-BOM).
// The UI uses only the public API; every state change carries an Idempotency-Key (ApiClient) and the company header.
import type { ApiClient } from "@lib/api/client";
import type { Page } from "../api";

export type BomStatus = "DRAFT" | "ACTIVE" | "OBSOLETE";

export interface BomLineDto {
  line_no: number;
  item_id: string;
  item_code: string;
  quantity: { value: string; uom: string };
  scrap_percent: string;
  cost_share_percent: string | null;
}

export interface BomDto {
  id: string;
  code: string;
  name: string;
  bom_status: BomStatus;
  bom_type: "KIT" | "PRODUCTION";
  item_id: string;
  version_no: number;
  valid_from: string;
  valid_to: string | null;
  output_qty: string;
  yield_percent: string;
  components: { lines: BomLineDto[]; byproducts: BomLineDto[] };
  version: number;
}

export interface BomLineInput {
  item_id: string;
  quantity: string;
  scrap_percent?: string;
  cost_share_percent?: string;
}

export interface BomInput {
  code: string;
  name: string;
  item_id: string;
  valid_from: string;
  valid_to?: string | null;
  output_qty: string;
  yield_percent: string;
  bom_type?: "KIT" | "PRODUCTION";
  components: { lines: BomLineInput[]; byproducts?: BomLineInput[] };
}

export interface ExplosionNodeDto {
  item_id: string;
  item_code: string;
  level: number;
  quantity: string;
  is_byproduct: boolean;
  children: ExplosionNodeDto[];
}

export interface ExplosionDto {
  bom_id: string;
  quantity: string;
  date: string;
  tree: ExplosionNodeDto;
  leaves: Array<{ item_id: string; item_code: string; quantity: string; unit_cost?: string; value?: { amount: string; currency: string } }>;
  total_cost?: { amount: string; currency: string };
}

export type BomAction = "activate" | "obsolete" | "copy-version";

const BASE = "/api/v1/inventory";

export class BomApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  list(signal?: AbortSignal): Promise<Page<BomDto>> {
    return this.api.get(`${BASE}/boms`, { params: { limit: 200, sort: "code,version_no" }, headers: this.headers(), signal });
  }

  create(body: BomInput): Promise<BomDto> {
    return this.api.post(`${BASE}/boms`, { body, headers: this.headers() });
  }

  action(id: string, action: BomAction, body: Record<string, unknown> = {}): Promise<BomDto> {
    return this.api.post(`${BASE}/boms/${id}:${action}`, { body, headers: this.headers() });
  }

  explode(id: string, qty: string, date: string, signal?: AbortSignal): Promise<ExplosionDto> {
    return this.api.get(`${BASE}/boms/${id}/explode`, { params: { qty, date }, headers: this.headers(), signal });
  }
}
