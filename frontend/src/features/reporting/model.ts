// Pure view model of the report builder (no I/O): query assembly from the UI state, es-CO formatting of results,
// and export links. Money and quantities stay strings end-to-end (Decimal on the server; never parsed to float).
import type { QueryFilter, ReportColumn, ReportResult, SemanticEntity, SemanticQuery } from "./api";

export interface BuilderState {
  entity: string;
  measures: string[];
  dimensions: string[];
  filters: QueryFilter[];
  timeGrain: string;
  asOf: string;
  compareTo: "" | "previous_period" | "previous_year";
  limit: number;
}

export const emptyBuilder = (entity = ""): BuilderState => ({
  entity, measures: [], dimensions: [], filters: [], timeGrain: "", asOf: "", compareTo: "", limit: 1000,
});

export function buildQuery(state: BuilderState): SemanticQuery {
  const q: SemanticQuery = { entity: state.entity, measures: [...state.measures] };
  if (state.dimensions.length) q.dimensions = [...state.dimensions];
  const filters = state.filters.filter((f) => f.field && (f.op === "is_null" || f.op === "not_null" || (f.value !== "" && f.value !== undefined)));
  if (filters.length) q.filters = filters;
  if (state.timeGrain) q.time_grain = state.timeGrain;
  if (state.asOf) q.as_of = state.asOf;
  if (state.compareTo) q.compare_to = state.compareTo;
  if (state.limit !== 1000) q.limit = state.limit;
  return q;
}

/** Dimensions offered for an entity: plain fields, plus ":root"/":parent" for hierarchies and grains for dates. */
export function dimensionOptions(entity: SemanticEntity): Array<{ value: string; label: string }> {
  const out: Array<{ value: string; label: string }> = [];
  for (const f of entity.fields) {
    out.push({ value: f.key, label: f.label });
    if (f.hierarchy) {
      out.push({ value: `${f.key}:root`, label: `${f.label} (grupo raíz)` });
      out.push({ value: `${f.key}:parent`, label: `${f.label} (grupo padre)` });
    }
  }
  return out;
}

/** es-CO grouping of a Decimal string without floating point: "1234567.5" → "1.234.567,50". */
export function formatDecimal(value: string, places = 2): string {
  const neg = value.startsWith("-");
  const raw = neg ? value.slice(1) : value;
  const [whole = "0", frac = ""] = raw.split(".");
  let digits = (frac + "0".repeat(places)).slice(0, places);
  let intPart = whole.replace(/^0+(?=\d)/, "");
  // round half up on the next digit (string arithmetic)
  const next = frac.charAt(places);
  if (next && next >= "5") {
    const n = (BigInt(intPart + digits) + 1n).toString().padStart(intPart.length + places, "0");
    intPart = n.slice(0, n.length - places) || "0";
    digits = n.slice(n.length - places);
  }
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const isZero = /^0*$/.test(intPart + digits);
  return `${neg && !isZero ? "-" : ""}${grouped}${places ? `,${digits}` : ""}`;
}

export function formatCell(column: ReportColumn, row: Record<string, unknown>): string {
  const label = row[`${column.key}__label`];
  if (typeof label === "string" && label) return label;
  const v = row[column.key];
  if (v === null || v === undefined) return row.suppressed ? "— (oculto, k < 5)" : "—";
  if (column.type === "money") return `$ ${formatDecimal(String(v))}`;
  if (column.type === "decimal") return formatDecimal(String(v));
  if (typeof v === "boolean") return v ? "Sí" : "No";
  return String(v);
}

export function isNumeric(column: ReportColumn): boolean {
  return column.type === "money" || column.type === "decimal" || column.type === "integer";
}

export function totalsLine(result: ReportResult): Array<{ label: string; value: string }> {
  return result.columns
    .filter((c) => c.key in result.totals)
    .map((c) => ({ label: c.label, value: formatCell(c, result.totals) }));
}

/** CSV of what the server returned (already policy-filtered): ";" separator and BOM for es-CO spreadsheets. */
export function toCsv(result: ReportResult): string {
  const esc = (s: string) => (/[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const lines = [result.columns.map((c) => esc(c.label)).join(";")];
  for (const row of result.rows) lines.push(result.columns.map((c) => esc(raw(c, row))).join(";"));
  return "\ufeff" + lines.join("\r\n");
}

function raw(column: ReportColumn, row: Record<string, unknown>): string {
  const label = row[`${column.key}__label`];
  if (typeof label === "string" && label) return label;
  const v = row[column.key];
  return v === null || v === undefined ? "" : String(v);
}
