// Typed calls to the barcode / label API (contracts/openapi/modules/inventory.yaml, F-INV-BARCODE).
import type { ApiClient } from "@lib/api/client";

export type OwnerType = "item" | "presentation" | "location" | "lot";

export interface BarcodeDto {
  id: string;
  value: string;
  symbology: "CODE128" | "EAN13" | "GTIN14_IMPORTED" | "QR";
  owner_type: OwnerType;
  owner_id: string;
  source: "GENERATED_INTERNAL" | "GENERATED_GS1_PREFIX" | "IMPORTED";
  gs1_ownership_claimed: boolean;
  label: string | null;
}

export interface GenerateResult {
  id: string;
  status: string;
  result: BarcodeDto;
}

export interface ScanResolution {
  owner_type: OwnerType;
  owner_id: string;
  item_id: string | null;
  presentation_id: string | null;
  location_id: string | null;
  warehouse_id: string | null;
  lot_id: string | null;
  code?: string;
  name?: string;
  factor?: string;
  value: string;
}

export interface LabelTemplateDto {
  id: string;
  code: string;
  name: string;
  symbology: string;
  layout: { fields: string[] };
  size_mm: string;
  status: string;
}

export interface LabelDto {
  value: string;
  symbology: string;
  modules: string;
  text: Record<string, string>;
}

export interface LabelJobDto {
  id: string;
  status: "QUEUED" | "RENDERED" | "DOWNLOADED";
  template_id: string;
  label_count: number;
  labels: LabelDto[];
  data_hash: string | null;
}

const BASE = "/api/v1/inventory";

export class BarcodesApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  generate(owner_type: OwnerType, owner_id: string, symbology: "CODE128" | "EAN13" = "CODE128", gs1_prefix?: string): Promise<GenerateResult> {
    const parameters: Record<string, string> = { owner_type, owner_id, symbology };
    if (gs1_prefix) parameters.gs1_prefix = gs1_prefix;
    return this.api.post(`${BASE}/barcodes:generate`, { body: { parameters }, headers: this.headers() });
  }

  importGtin(owner_type: OwnerType, owner_id: string, value: string): Promise<GenerateResult> {
    return this.api.post(`${BASE}/barcodes:generate`, { body: { parameters: { owner_type, owner_id, mode: "import", value } }, headers: this.headers() });
  }

  validate(value: string): Promise<{ status: string; data: { value: string; symbology: string } }> {
    return this.api.post(`${BASE}/barcodes:validate`, { body: { parameters: { value } }, headers: this.headers() });
  }

  lookup(value: string, signal?: AbortSignal): Promise<{ status: string; data: ScanResolution }> {
    return this.api.get(`${BASE}/barcodes/lookup`, { params: { value }, headers: this.headers(), signal });
  }

  templates(signal?: AbortSignal): Promise<{ items: LabelTemplateDto[] }> {
    return this.api.get(`${BASE}/label-templates`, { params: { limit: 100, sort: "code" }, headers: this.headers(), signal });
  }

  createTemplate(body: { code: string; name: string; symbology: string; layout: { fields: string[] }; size_mm: string }): Promise<LabelTemplateDto> {
    return this.api.post(`${BASE}/label-templates`, { body, headers: this.headers() });
  }

  printJob(template_id: string, targets: Partial<Record<"items" | "presentations" | "locations" | "lots", Array<{ id: string; copies: number }>>>, copies = 1): Promise<LabelJobDto> {
    return this.api.post(`${BASE}/label-print-jobs`, { body: { template_id, targets, copies }, headers: this.headers() });
  }
}
