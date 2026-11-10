// Typed calls to the CRM API (contracts/openapi/modules/crm.yaml). State changes go through ApiClient (Idempotency-Key).
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}
export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
export interface StageDto {
  code: string;
  name: string;
  probability: string;
}
export interface PipelineDto {
  id: string;
  code: string;
  name: string;
  status: string;
  stages: StageDto[];
}
export interface OpportunityDto {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  opportunity_status: "OPEN" | "WON" | "LOST";
  party: { id: string; kind: string } | null;
  /** Confidential: absent when the user's field policy does not allow it. */
  amount?: Money | null;
  weighted_amount?: Money | null;
  stage: string;
  close_date: string | null;
  probability: string;
  pipeline_id: string;
  owner_id: string | null;
}

export class CrmApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }

  pipelines(): Promise<Page<PipelineDto>> {
    return this.api.get("/api/v1/crm/pipelines", { headers: this.headers() });
  }
  opportunities(pipelineId: string): Promise<Page<OpportunityDto>> {
    return this.api.get("/api/v1/crm/opportunities", { headers: this.headers(), params: { pipeline_id: pipelineId, limit: "500" } });
  }
  moveStage(id: string, stage: string, etag: string): Promise<OpportunityDto> {
    return this.api.patch(`/api/v1/crm/opportunities/${id}`, { headers: { ...this.headers(), "If-Match": etag }, body: { stage } });
  }
  close(id: string, won: boolean, reason = ""): Promise<OpportunityDto> {
    return this.api.post(`/api/v1/crm/opportunities/${id}:${won ? "win" : "lose"}`, { headers: this.headers(), body: reason ? { reason } : {} });
  }
}
