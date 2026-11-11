// Typed calls to the documents API (contracts/openapi/modules/documents.yaml, D-005). Templates are sandboxed markup
// validated by the server; previews are never persisted; renders are stored server-side with their SHA-256.
import type { ApiClient } from "@lib/api/client";

export interface TemplateDto {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  document_type: string;
  engine: "weasyprint";
  body: string;
  locale: string;
  version: number;
}
export interface PreviewDto {
  persisted: false;
  documents: Array<{ template_code: string; template_version: string; media_type: string; sha256: string; pages: number; html: string }>;
  explain: string[];
}

export class DocumentsApi {
  constructor(
    private readonly api: ApiClient,
    private readonly companyId: string | null,
  ) {}

  private headers(): Record<string, string> {
    return this.companyId ? { "X-Company-Id": this.companyId } : {};
  }
  templates(): Promise<{ items: TemplateDto[]; next_cursor: string | null }> {
    return this.api.get("/api/v1/documents/templates", { headers: this.headers() });
  }
  create(input: Pick<TemplateDto, "code" | "name" | "document_type" | "body">): Promise<TemplateDto> {
    return this.api.post("/api/v1/documents/templates", { headers: this.headers(), body: { ...input, engine: "weasyprint", locale: "es-CO" } });
  }
  update(t: TemplateDto, body: string, etag: string): Promise<TemplateDto> {
    return this.api.patch(`/api/v1/documents/templates/${t.id}`, { headers: this.headers(), body: { body }, ifMatch: etag });
  }
  preview(id: string, sample?: Record<string, unknown>): Promise<PreviewDto> {
    return this.api.post(`/api/v1/documents/templates/${id}:preview`, { headers: this.headers(), body: sample ? { parameters: { sample } } : {} });
  }
}
