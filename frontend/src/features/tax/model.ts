/**
 * Pure view-model logic for the tax screens. No rate is ever written in the UI: every value comes
 * from `/api/v1/tax/parameters` with its status and source (C1-9).
 */

export type ParameterStatus = "VERIFIED-PRIMARY" | "VERIFIED-SEARCH" | "VERIFIED-SECONDARY" | "UNKNOWN" | "DISPUTED";
export type Approval = "PROPOSED" | "APPROVED" | "REJECTED" | "SUPERSEDED";

export interface LegalEvent {
  event: string;
  date: string;
  source?: string;
  note?: string;
}

export interface Parameter {
  id: string;
  code: string;
  value: { type: string; value?: string; rows?: Record<string, string | null>[]; data?: Record<string, unknown> };
  valid_from: string;
  valid_to: string | null;
  source_id: string;
  parameter_status: ParameterStatus;
  legal_reviewed_by: string | null;
  legal_reviewed_at: string | null;
  synthetic: boolean;
  approval: Approval;
  proposed_by: string | null;
  legal_history: LegalEvent[];
  notes: string;
  version: number;
}

export interface TimelineEntry {
  parameter: Parameter;
  current: boolean;
}

export interface Timeline {
  code: string;
  entries: TimelineEntry[];
  /** True when the value effective on the reference date is missing or UNKNOWN: calculations fail closed. */
  blocking: boolean;
  disputed: boolean;
}

export const STATUS_LABEL: Record<ParameterStatus, string> = {
  "VERIFIED-PRIMARY": "Verificado (fuente oficial)",
  "VERIFIED-SEARCH": "Verificado (extracto de búsqueda)",
  "VERIFIED-SECONDARY": "Verificado (fuente secundaria)",
  UNKNOWN: "Desconocido — bloquea el cálculo",
  DISPUTED: "En disputa — se calcula con advertencia",
};

export function isEffective(p: Parameter, onDate: string): boolean {
  return p.valid_from <= onDate && (p.valid_to === null || p.valid_to >= onDate);
}

export function effectiveValue(params: Parameter[], code: string, onDate: string): Parameter | null {
  const candidates = params
    .filter((p) => p.code === code && p.approval === "APPROVED" && isEffective(p, onDate))
    .sort((a, b) => (a.valid_from < b.valid_from ? 1 : -1));
  return candidates[0] ?? null;
}

/** Group parameters by code into timelines (newest first) and flag what blocks on `onDate`. */
export function buildTimelines(params: Parameter[], onDate: string): Timeline[] {
  const byCode = new Map<string, Parameter[]>();
  for (const p of params) {
    byCode.set(p.code, [...(byCode.get(p.code) ?? []), p]);
  }
  return [...byCode.entries()]
    .sort(([a], [b]) => a.localeCompare(b, "es-CO"))
    .map(([code, rows]) => {
      const current = effectiveValue(rows, code, onDate);
      return {
        code,
        entries: [...rows]
          .sort((a, b) => (a.valid_from < b.valid_from ? 1 : -1))
          .map((parameter) => ({ parameter, current: current?.id === parameter.id })),
        blocking: current === null || current.parameter_status === "UNKNOWN",
        disputed: current?.parameter_status === "DISPUTED",
      };
    });
}

/** UNKNOWN or missing values on the date, shown as blocking items at the top of every tax page. */
export function blockingItems(timelines: Timeline[]): string[] {
  return timelines.filter((t) => t.blocking).map((t) => t.code);
}

export function needsLegalReview(p: Parameter): boolean {
  return p.approval === "APPROVED" && (p.legal_reviewed_by === null || p.synthetic);
}

/** Display of a stored value, never a computed or hard-coded rate. */
export function displayValue(p: Parameter): string {
  const v = p.value;
  if (v.type === "unknown") return "— (desconocido)";
  if (v.type === "percent") return `${formatDecimal(v.value ?? "")} %`;
  if (v.type === "permil") return `${formatDecimal(v.value ?? "")} ‰`;
  if (v.type === "uvt") return `${formatDecimal(v.value ?? "")} UVT`;
  if (v.type === "decimal") return formatDecimal(v.value ?? "");
  if (v.type === "table") return `Tabla (${v.rows?.length ?? 0} filas)`;
  return "Objeto JSON";
}

/** es-CO grouping on a decimal string without converting it to a binary float. */
export function formatDecimal(raw: string): string {
  const negative = raw.startsWith("-");
  const [whole, frac] = (negative ? raw.slice(1) : raw).split(".");
  const grouped = (whole || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negative ? "-" : ""}${grouped}${frac ? `,${frac}` : ""}`;
}

export interface TraceStep {
  step: string;
  line_ref: string | null;
  tax_code: string | null;
  detail: string;
  data: Record<string, unknown>;
}

/** "¿Por qué este impuesto?": the trace steps that explain one computed line. */
export function explain(trace: TraceStep[], lineRef: string, taxCode: string): TraceStep[] {
  return trace.filter(
    (s) => (s.line_ref === lineRef || s.line_ref === null) && (s.tax_code === taxCode || s.step === "document"),
  );
}
