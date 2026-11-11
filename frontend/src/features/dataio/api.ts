// Typed client of /api/v1/dataio (contract dataio.yaml 1.0.0), owner P18. Every call goes through the shell's
// ApiClient (auth, Idempotency-Key on writes) and carries the active company (X-Company-Id). Files travel as
// base64 JSON (the shell client speaks JSON only; CCR P18-3) — the same endpoints also accept multipart.
import type { ApiClient } from "@lib/api/client";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface TemplateColumn {
  name: string;
  type: string;
  required: boolean;
  help: string;
  allowed?: string[];
  max_length?: number;
  example?: string;
  line?: boolean;
  permission?: string;
}

export interface Template {
  id: string;
  entity: string;
  label: string;
  module: string;
  natural_key: string[];
  document: boolean;
  hierarchy: boolean;
  updatable: boolean;
  columns: TemplateColumn[];
  notes: string;
}

export interface RowError {
  row: number;
  code: string;
  message: string;
  column?: string;
}

export interface ImportJob {
  id: string;
  entity: string;
  mode: string;
  import_status: "UPLOADED" | "MAPPED" | "VALIDATED" | "COMMITTED" | "FAILED" | "CANCELLED";
  filename: string;
  row_count: number;
  error_count: number;
  applied_rows: number;
  counts: Partial<Record<"created" | "updated" | "unchanged" | "failed", number>>;
  headers?: string[];
  mapping?: Record<string, string>;
  errors?: RowError[];
  preview?: Array<{ rows: number[]; key: string[]; action: string }>;
  audit?: { created_at: string };
}

export interface ExportJob {
  id: string;
  source: string;
  format: "csv" | "xlsx" | "jsonl";
  export_status: string;
  row_count: number;
  filename: string;
  omitted_columns: string[];
  audit?: { created_at: string };
}

export interface FilePayload {
  filename: string;
  content_type: string;
  content_base64: string;
}

const BASE = "/api/v1/dataio";

export function dataioApi(api: ApiClient, companyId: string | null) {
  const headers = companyId ? { "X-Company-Id": companyId } : undefined;
  return {
    templates: () => api.get<Page<Template>>(`${BASE}/templates`, { params: { limit: 500 }, headers }),
    templateFile: (entity: string, file_format: string) =>
      api.get<FilePayload>(`${BASE}/templates/${entity}/download`, { params: { file_format, encoding: "base64" }, headers }),
    imports: (params: Record<string, string> = {}) => api.get<Page<ImportJob>>(`${BASE}/imports`, { params, headers }),
    importJob: (id: string) => api.get<ImportJob>(`${BASE}/imports/${id}`, { headers }),
    createImport: (entity: string, mode: string) => api.post<ImportJob>(`${BASE}/imports`, { body: { entity, mode }, headers }),
    upload: (id: string, filename: string, content_base64: string) =>
      api.post<ImportJob>(`${BASE}/imports/${id}:upload`, { body: { filename, content_base64 }, headers }),
    validate: (id: string) => api.post<ImportJob>(`${BASE}/imports/${id}:validate`, { body: {}, headers }),
    commit: (id: string) => api.post<{ status: string; import: ImportJob }>(`${BASE}/imports/${id}:commit`, { body: {}, headers }),
    cancel: (id: string) => api.post<ImportJob>(`${BASE}/imports/${id}:cancel`, { body: {}, headers }),
    report: (id: string, file_format: string) =>
      api.get<FilePayload>(`${BASE}/imports/${id}/report`, { params: { file_format, encoding: "base64" }, headers }),
    exports: () => api.get<Page<ExportJob>>(`${BASE}/exports`, { headers }),
    createExport: (source: string, format: string) => api.post<ExportJob>(`${BASE}/exports`, { body: { source, format }, headers }),
    exportFile: (id: string) => api.get<FilePayload>(`${BASE}/exports/${id}/file`, { params: { encoding: "base64" }, headers }),
  };
}

/** ArrayBuffer → base64 (chunked; no String.fromCharCode overflow on large files). */
export function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export function fromBase64(b64: string): Uint8Array<ArrayBuffer> {
  const bin = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function downloadPayload(p: FilePayload): void {
  const blob = new Blob([fromBase64(p.content_base64)], { type: p.content_type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = p.filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function problemText(code: string | undefined, fallback: string): string {
  const known: Record<string, string> = {
    import_validation_failed: "El archivo tiene errores por fila; no se aplicó ninguna fila.",
    import_duplicate_batch: "Este lote ya se importó en la empresa.",
    import_file_rejected: "Archivo rechazado (tamaño, formato, columnas reservadas o contenido inseguro).",
    template_unknown: "Plantilla desconocida.",
    invalid_state_transition: "La acción no está permitida en el estado actual.",
    policy_denied: "No tiene permiso para esta acción.",
  };
  return (code && known[code]) || fallback;
}
