// Typed calls to the reporting API (contracts/openapi/modules/reporting.yaml, P17a core). The UI uses only the public
// API: every query goes through the server's semantic layer (row/field policy); state changes send an Idempotency-Key.
import type { ApiClient } from "@lib/api/client";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export type FieldType = "uuid" | "string" | "date" | "integer" | "money" | "decimal" | "boolean";
export interface SemanticField {
  key: string;
  label: string;
  type: FieldType;
  classification: string;
  hierarchy: string | null;
  time: boolean;
}
export interface SemanticMeasure {
  key: string;
  label: string;
  type: FieldType;
  aggregation: string;
  sensitive: boolean;
}
export interface SemanticEntity {
  entity: string;
  label: string;
  fields: SemanticField[];
  measures: SemanticMeasure[];
  time_grains: string[];
  k_anonymity: number | null;
}
export type FilterOp =
  | "eq" | "ne" | "in" | "not_in" | "gt" | "gte" | "lt" | "lte" | "between" | "is_null" | "not_null" | "contains" | "in_subtree";
export interface QueryFilter {
  field: string;
  op: FilterOp;
  value?: unknown;
}
export interface SemanticQuery {
  entity: string;
  measures: string[];
  dimensions?: string[];
  filters?: QueryFilter[];
  sort?: Array<{ field: string; dir: "asc" | "desc" }>;
  limit?: number;
  time_grain?: string;
  compare_to?: "previous_period" | "previous_year";
  as_of?: string;
}
export interface ReportColumn {
  key: string;
  label: string;
  type: string;
}
export interface ReportResult {
  report: string;
  columns: ReportColumn[];
  rows: Array<Record<string, unknown>>;
  totals: Record<string, unknown>;
  warnings: Array<{ code: string; detail: string }>;
  synthetic: boolean;
  truncated?: boolean;
  run_id?: string;
}
export interface SavedReportDto {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  definition: { query: SemanticQuery; view?: string; published?: boolean; share_roles?: string[] };
  schedule: string | null;
  recipients: string[];
  version: number;
}

export class ReportingApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  async semanticModel(): Promise<SemanticEntity[]> {
    const r = await this.api.get<{ entities: SemanticEntity[] }>("/api/v1/reporting/semantic-model", { headers: this.headers() });
    return r.entities;
  }
  query(query: SemanticQuery): Promise<ReportResult> {
    return this.api.post("/api/v1/reporting/query", { headers: this.headers(), body: { parameters: query } });
  }
  explain(query: SemanticQuery): Promise<{ status: string; data: { estimate: { total_cost: number }; interactive: boolean } }> {
    return this.api.post("/api/v1/reporting/query:explain", { headers: this.headers(), body: { parameters: query } });
  }
  reports(): Promise<Page<SavedReportDto>> {
    return this.api.get("/api/v1/reporting/reports", { headers: this.headers() });
  }
  createReport(code: string, name: string, query: SemanticQuery, view = "table"): Promise<SavedReportDto> {
    return this.api.post("/api/v1/reporting/reports", { headers: this.headers(), body: { code, name, definition: { query, view } } });
  }
  act(id: string, action: "run" | "publish" | "archive" | "schedule", parameters: Record<string, unknown> = {}): Promise<ReportResult & { data?: SavedReportDto }> {
    return this.api.post(`/api/v1/reporting/reports/${id}:${action}`, { headers: this.headers(), body: { parameters } });
  }
}
