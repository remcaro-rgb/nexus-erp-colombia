// Typed client of /api/v1/consolidation and /api/v1/intercompany (contracts consolidation.yaml and
// intercompany.yaml 1.0.0), owner P16b. Every call goes through the shell's ApiClient (auth, Idempotency-Key on
// writes). Groups and agreements are tenant level: the server checks every company they touch.
import type { ApiClient } from "@lib/api/client";

export interface Money {
  amount: string;
  currency: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ReportColumn {
  key: string;
  label: string;
  type: string;
}

export interface ReportResult {
  report: string;
  parameters?: Record<string, unknown>;
  columns: ReportColumn[];
  rows: Array<Record<string, string | number | boolean | null>>;
  totals?: Record<string, string | number>;
  warnings?: Array<{ code: string; message: string }>;
  synthetic?: boolean;
}

export interface ConsolidationGroup {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  companies: string[];
  presentation_currency: string;
  version: number;
}

export interface ConsolidationRun {
  id: string;
  number: string;
  status: "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED" | "POSTED" | "REVERSED" | "CANCELLED";
  document_date: string;
  posting_date: string | null;
  group_id: string;
  period: string;
  currency: string;
  total: Money;
  version: number;
}

export interface IntercompanyAgreement {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  company_a_id: string;
  company_b_id: string;
  accounts: Record<string, string | boolean | null>;
  version: number;
}

export interface SimulationResult {
  persisted: false;
  journal_entries: Array<{ journal: string; posting_date: string; memo?: string; lines: Array<{ account: string; debit: Money; credit: Money }> }>;
  explain?: string[];
  warnings?: Array<{ code: string; message: string }>;
}

export const BASE = "/api/v1/consolidation";
export const IC_BASE = "/api/v1/intercompany";
export const PERIOD_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function consolidationApi(api: ApiClient) {
  return {
    groups: () => api.get<Page<ConsolidationGroup>>(`${BASE}/groups`, { params: { limit: 500 } }),
    createGroup: (body: { code: string; name: string; companies: string[]; presentation_currency: string }) =>
      api.post<ConsolidationGroup>(`${BASE}/groups`, { body }),
    runs: () => api.get<Page<ConsolidationRun>>(`${BASE}/runs`, { params: { limit: 500 } }),
    createRun: (body: { group_id: string; period: string; document_date: string }) => api.post<ConsolidationRun>(`${BASE}/runs`, { body }),
    preview: (id: string) => api.post<SimulationResult>(`${BASE}/runs/${id}:preview`, { body: {} }),
    post: (id: string, etag?: string) => api.post<ConsolidationRun>(`${BASE}/runs/${id}:post`, { body: {}, ifMatch: etag }),
    report: (name: "balance" | "income-statement" | "eliminations", runId?: string) =>
      api.get<ReportResult>(`${BASE}/reports/${name}`, { params: { run_id: runId } }),
    agreements: () => api.get<Page<IntercompanyAgreement>>(`${IC_BASE}/agreements`, { params: { limit: 500 } }),
    createAgreement: (body: { code: string; name: string; company_a_id: string; company_b_id: string; accounts: Record<string, unknown> }) =>
      api.post<IntercompanyAgreement>(`${IC_BASE}/agreements`, { body }),
    reconciliation: (period?: string) => api.get<ReportResult>(`${IC_BASE}/reconciliation`, { params: { period } }),
  };
}

/** CSV export URL of a report (the server renders it with the same permission checks). */
export function exportUrl(path: string, params: Record<string, string | undefined>): string {
  const qs = new URLSearchParams({ format: "csv" });
  for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v);
  return `${path}?${qs.toString()}`;
}

export interface GridRow {
  id: string;
  [key: string]: string | number | boolean | null;
}

/** ReportResult rows → grid rows with a stable id (the grid needs `id`). */
export function reportRows(report: ReportResult | null | undefined): GridRow[] {
  return (report?.rows ?? []).map((r, i) => ({ ...r, id: String(r.entry_no ?? r.agreement_id ?? r.account ?? "") + `#${i}` }));
}

export function validateAgreement(form: { code: string; name: string; company_a_id: string; company_b_id: string }, msg: { required: string; uuid: string; same: string }): Record<string, string> {
  const errs: Record<string, string> = {};
  if (!form.code.trim()) errs.code = msg.required;
  if (!form.name.trim()) errs.name = msg.required;
  for (const k of ["company_a_id", "company_b_id"] as const) {
    if (!form[k].trim()) errs[k] = msg.required;
    else if (!UUID_RE.test(form[k].trim())) errs[k] = msg.uuid;
  }
  if (!errs.company_b_id && form.company_a_id.trim() && form.company_a_id.trim() === form.company_b_id.trim()) errs.company_b_id = msg.same;
  return errs;
}

export function validateRun(form: { group_id: string; period: string; document_date: string }, msg: { required: string; period: string }): Record<string, string> {
  const errs: Record<string, string> = {};
  if (!form.group_id) errs.group_id = msg.required;
  if (!PERIOD_RE.test(form.period)) errs.period = msg.period;
  if (!form.document_date) errs.document_date = msg.required;
  return errs;
}

/** Last day of a YYYY-MM period (default document date of a run). */
export function periodEnd(period: string): string {
  if (!PERIOD_RE.test(period)) return "";
  const [y, m] = period.split("-").map(Number) as [number, number];
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${period}-${String(last).padStart(2, "0")}`;
}

const PROBLEMS: Record<string, string> = {
  policy_denied: "No tiene permiso para esta acción.",
  sod_violation: "Segregación de funciones: quien prepara la consolidación no puede contabilizarla.",
  feature_disabled: "La consolidación ([A]) no está habilitada para este tenant.",
  fx_rate_missing: "Falta la tasa de cambio para la moneda de presentación.",
  period_closed: "El período de consolidación está cerrado.",
  invalid_state_transition: "Acción no permitida en el estado actual.",
  document_immutable: "Documento contabilizado: corrija con un reverso.",
  validation_failed: "Revise los datos marcados.",
  intercompany_mismatch: "Hay diferencias intercompañía por conciliar.",
};

export function problemText(code: string | undefined, fallback: string): string {
  return (code && PROBLEMS[code]) || fallback;
}
