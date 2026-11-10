// Typed calls to the inventory API (contracts/openapi/modules/inventory.yaml, F-INV-LOC / F-INV-PRES / transfers).
// The UI uses only the public API; every state change carries an Idempotency-Key (ApiClient) and the company header.
import type { ApiClient } from "@lib/api/client";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface WarehouseDto {
  id: string;
  code: string;
  name: string;
  status: string;
  branch_id: string;
  root_location_id: string | null;
  transit_location_id: string | null;
}

export interface LocationNodeDto {
  id: string;
  code: string;
  name: string;
  location_type: "VIEW" | "INTERNAL" | "TRANSIT" | "SCRAP";
  status: "ACTIVE" | "ARCHIVED";
  path: string;
  parent_id: string | null;
  on_hand: string;
  is_leaf: boolean;
  children: LocationNodeDto[];
}

export interface PackagedDto {
  presentation_id: string;
  code: string;
  factor: string;
  packages: string;
  reserved_packages: string;
  available_packages: string;
}

export interface AvailabilityDto {
  item_id: string;
  warehouse_id: string;
  location_id: string;
  location_code: string;
  location_type: string;
  lot_id: string | null;
  on_hand: string;
  reserved: string;
  available: string;
  packaged_base: string;
  loose: string;
  packaged: PackagedDto[];
}

export interface ItemDto {
  id: string;
  code: string;
  name: string;
  base_uom: string;
  status: string;
}

export interface PresentationDto {
  id: string;
  item_id: string;
  code: string;
  name: string;
  factor: string;
  uom: string;
  barcode: string | null;
  precision: number;
  status: string;
  version: number;
}

export interface PresentationInput {
  code: string;
  name: string;
  uom: string;
  factor: string;
  precision?: number;
  barcode?: string | null;
}

export interface TransferDto {
  id: string;
  number: string;
  document_date: string;
  status: "DRAFT" | "SUBMITTED" | "APPROVED" | "SHIPPED" | "RECEIVED" | "CANCELLED";
  from_warehouse_id: string;
  to_warehouse_id: string;
  total?: { amount: string; currency: string } | null;
}

export type TransferAction = "submit" | "approve" | "ship" | "receive" | "cancel";

export interface InternalMoveInput {
  item_id: string;
  from_location_id: string;
  to_location_id: string;
  quantity: string;
  lot_id?: string | null;
}

const BASE = "/api/v1/inventory";

export class InventoryApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  warehouses(signal?: AbortSignal): Promise<Page<WarehouseDto>> {
    return this.api.get(`${BASE}/warehouses`, { params: { limit: 500, sort: "code" }, headers: this.headers(), signal });
  }

  tree(warehouseId: string, signal?: AbortSignal): Promise<{ warehouse_id: string; items: LocationNodeDto[] }> {
    return this.api.get(`${BASE}/warehouses/${warehouseId}/locations/tree`, { headers: this.headers(), signal });
  }

  availability(params: { item?: string; presentation?: string; warehouse?: string; location?: string; lot?: string }, signal?: AbortSignal): Promise<Page<AvailabilityDto>> {
    return this.api.get(`${BASE}/availability`, { params, headers: this.headers(), signal });
  }

  items(q: string, signal?: AbortSignal): Promise<Page<ItemDto>> {
    return this.api.get(`${BASE}/items`, { params: { limit: 50, q: q || undefined, sort: "code" }, headers: this.headers(), signal });
  }

  presentations(itemId: string, signal?: AbortSignal): Promise<Page<PresentationDto>> {
    return this.api.get(`${BASE}/items/${itemId}/presentations`, { params: { limit: 500, sort: "code" }, headers: this.headers(), signal });
  }

  createPresentation(itemId: string, body: PresentationInput): Promise<PresentationDto> {
    return this.api.post(`${BASE}/items/${itemId}/presentations`, { body, headers: this.headers() });
  }

  internalMove(body: InternalMoveInput): Promise<{ move: { id: string } }> {
    return this.api.post(`${BASE}/moves:internal`, { body, headers: this.headers() });
  }

  transfers(signal?: AbortSignal): Promise<Page<TransferDto>> {
    return this.api.get(`${BASE}/transfers`, { params: { limit: 100, sort: "-document_date" }, headers: this.headers(), signal });
  }

  transferAction(id: string, action: TransferAction): Promise<TransferDto> {
    return this.api.post(`${BASE}/transfers/${id}:${action}`, { body: {}, headers: this.headers() });
  }
}
