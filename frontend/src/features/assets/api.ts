// Typed client of /api/v1/assets (contract assets.yaml 1.0.0), owner P14. Every call goes through the shell's
// ApiClient (auth, Idempotency-Key on writes) and carries the active company (X-Company-Id).
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}

export interface ReportRow {
  [key: string]: string | boolean | null;
}

export interface ReportResult {
  report: string;
  columns: Array<{ key: string; label: string; type: string }>;
  rows: ReportRow[];
  totals?: Record<string, string>;
  synthetic?: boolean;
}

export interface RegisterRow {
  id: string;
  asset_id: string;
  code: string;
  name: string;
  category: string;
  book: string;
  status: string;
  location: string | null;
  cost: string;
  accumulated_depreciation: string;
  accumulated_impairment: string;
  net_book_value: string;
}

export interface AssetBook {
  id: string;
  code: string;
  name: string;
  framework: "NIIF" | "FISCAL";
}

export interface AssetCategory {
  id: string;
  code: string;
  name: string;
  useful_life_months: number;
  method: string;
}

export interface DepreciationRun {
  id: string;
  number: string;
  status: string;
  document_date: string;
  period_id: string;
  book_id: string;
  total: Money;
  lines?: Array<{ line_no: number; item_id?: string; description?: string; amount?: Money }>;
}

export interface Simulation {
  persisted: false;
  documents: Array<Record<string, string>>;
  journal_entries: Array<{ journal: string; posting_date: string; lines: Array<{ account: string; debit: Money; credit: Money }> }>;
  explain?: string[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export const BASE = "/api/v1/assets";

export function assetsApi(api: ApiClient, companyId: string | null) {
  const headers = companyId ? { "X-Company-Id": companyId } : undefined;
  return {
    register: (params: { book_id?: string; as_of?: string } = {}) => api.get<ReportResult>(`${BASE}/reports/register`, { params, headers }),
    books: () => api.get<Page<AssetBook>>(`${BASE}/books`, { headers }),
    categories: () => api.get<Page<AssetCategory>>(`${BASE}/categories`, { params: { limit: 500 }, headers }),
    runs: (params: Record<string, string> = {}) => api.get<Page<DepreciationRun>>(`${BASE}/depreciation-runs`, { params: { sort: "-document_date", ...params }, headers }),
    createRun: (body: { document_date: string; period_id: string; book_id: string }) => api.post<DepreciationRun>(`${BASE}/depreciation-runs`, { body, headers }),
    previewRun: (id: string) => api.post<Simulation>(`${BASE}/depreciation-runs/${id}:preview`, { body: {}, headers }),
    postRun: (id: string) => api.post<DepreciationRun>(`${BASE}/depreciation-runs/${id}:post`, { body: {}, headers }),
    createAsset: (body: Record<string, unknown>) => api.post<{ id: string; code: string }>(`${BASE}/assets`, { body, headers }),
    activate: (id: string, parameters: Record<string, unknown>) => api.post<{ id: string }>(`${BASE}/assets/${id}:activate`, { body: { parameters }, headers }),
    dispose: (id: string, reason: string, effective_date: string, parameters: Record<string, unknown>) =>
      api.post<{ id: string }>(`${BASE}/assets/${id}:dispose`, { body: { reason, effective_date, parameters }, headers }),
    transfer: (id: string, reason: string, effective_date: string, parameters: Record<string, unknown>) =>
      api.post<{ id: string }>(`${BASE}/assets/${id}:transfer`, { body: { reason, effective_date, parameters }, headers }),
  };
}

export type AssetsApi = ReturnType<typeof assetsApi>;

/** Register report rows (one per asset and book) → grid rows with a stable id. */
export function registerRows(report: ReportResult | null, book?: string): RegisterRow[] {
  return (report?.rows ?? [])
    .filter((r) => !book || r.book === book)
    .map((r) => ({ ...(r as unknown as RegisterRow), id: `${String(r.asset_id)}:${String(r.book)}` }));
}

/** Unique assets of the register (labels are printed once per asset, not per book). */
export function labelItems(report: ReportResult | null): Array<{ code: string; name: string; location: string | null }> {
  const seen = new Map<string, { code: string; name: string; location: string | null }>();
  for (const r of report?.rows ?? []) {
    const code = String(r.code ?? "");
    if (code && r.status !== "DISPOSED" && !seen.has(code)) seen.set(code, { code, name: String(r.name ?? ""), location: (r.location as string | null) ?? null });
  }
  return [...seen.values()].sort((a, b) => a.code.localeCompare(b.code, "es-CO"));
}

/** es-CO text for a problem+json error code of this module (contracts/errors.yaml). */
export function problemText(code: string | undefined, fallback: string): string {
  const known: Record<string, string> = {
    asset_already_disposed: "El activo ya fue dado de baja.",
    depreciation_run_exists: "Ya existe una corrida de depreciación para ese libro y periodo.",
    document_immutable: "El documento está contabilizado; corrija con un documento nuevo.",
    invalid_state_transition: "La acción no está permitida en el estado actual.",
    period_closed: "El periodo contable está cerrado.",
    reason_required: "Indique el motivo.",
    sod_violation: "Otra persona debe aprobar (segregación de funciones).",
    policy_denied: "No tiene permiso para esta acción.",
    validation_failed: "Revise los datos ingresados.",
  };
  return (code && known[code]) || fallback;
}
