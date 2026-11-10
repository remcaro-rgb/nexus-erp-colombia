/**
 * Pure view-model logic of Talento › Nómina › Conceptos y fórmulas (F-PAY-FORMULA, P13b).
 * No legal value is written in the UI: every rate comes from the payslip / graph snapshot returned by
 * `/api/v1/payroll/*` with its status and source (C1-9). Fixture values are labelled NOT LEGALLY VERIFIED.
 */

export type ConceptType = "DEVENGO" | "DEDUCCION" | "APORTE" | "PROVISION";

export const NOT_LEGALLY_VERIFIED = "NOT LEGALLY VERIFIED";

export const CONCEPT_TYPE_LABEL: Record<ConceptType, string> = {
  DEVENGO: "Devengo",
  DEDUCCION: "Deducción",
  APORTE: "Aporte del empleador",
  PROVISION: "Provisión",
};

export interface PayrollConcept {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  concept_type: ConceptType;
  formula_id: string | null;
  depends_on: string[];
  account_role: string;
  salary_base: boolean;
  electronic_payroll_code: string | null;
  version: number;
}

export interface ParameterSnapshot {
  code: string;
  input?: string;
  value?: string;
  status: string;
  source_id?: string;
  valid_from?: string | null;
  valid_to?: string | null;
  legally_verified: boolean;
  label: string;
}

export interface Money {
  amount: string;
  currency: string;
}

export interface TraceEntry {
  expr: string;
  value: string;
  pos?: number;
}

export interface PayslipLine {
  concept: string;
  concept_type: ConceptType;
  sequence: number;
  amount: Money;
  formula_version: number | null;
  formula_hash: string;
  inputs: Record<string, string>;
  trace: TraceEntry[];
  parameters: ParameterSnapshot[];
}

export interface Payslip {
  id: string;
  run_id: string;
  status: "CALCULATED" | "POSTED";
  employee_id: string | null;
  employee_name: string | null;
  earnings: Money | null;
  deductions: Money | null;
  net_pay: Money | null;
  employer_cost: Money | null;
  lines: PayslipLine[] | null;
  legally_verified: boolean;
  legal_notice: string;
  parameters: ParameterSnapshot[];
  masked: string[];
}

export interface GraphValidation {
  status: "valid" | "invalid";
  data: {
    on: string;
    order: string[];
    errors: { code: string; detail: string; [k: string]: unknown }[];
    parameters: ParameterSnapshot[];
  };
}

/** The label shown next to a payslip, line or parameter: empty only for reviewed, non-synthetic values. */
export function legalLabel(item: { legally_verified: boolean; label?: string; legal_notice?: string }): string {
  if (item.legally_verified) return "";
  return item.label || item.legal_notice || NOT_LEGALLY_VERIFIED;
}

export function payslipNeedsLabel(slip: Pick<Payslip, "legally_verified" | "parameters">): boolean {
  return !slip.legally_verified || slip.parameters.some((p) => !p.legally_verified);
}

/**
 * Client-side mirror of the server's topological order (Kahn, ties by code) used only to draw the
 * dependency view before validation; the server's `:validate-graph` result is authoritative.
 * Returns `null` order and the cycle members when the declared dependencies contain a cycle.
 */
export function dependencyOrder(concepts: Pick<PayrollConcept, "code" | "depends_on">[]): { order: string[] | null; cycle: string[] } {
  const deps = new Map(concepts.map((c) => [c.code, [...new Set(c.depends_on)].sort()]));
  const indeg = new Map<string, number>();
  const dependents = new Map<string, string[]>();
  for (const [code, ds] of deps) {
    indeg.set(code, ds.filter((d) => deps.has(d)).length);
    for (const d of ds) {
      if (!dependents.has(d)) dependents.set(d, []);
      dependents.get(d)!.push(code);
    }
  }
  const ready = [...deps.keys()].filter((c) => indeg.get(c) === 0).sort();
  const order: string[] = [];
  while (ready.length) {
    const n = ready.shift()!;
    order.push(n);
    for (const m of (dependents.get(n) ?? []).sort()) {
      indeg.set(m, (indeg.get(m) ?? 0) - 1);
      if (indeg.get(m) === 0) {
        ready.push(m);
        ready.sort();
      }
    }
  }
  if (order.length !== deps.size) {
    return { order: null, cycle: [...deps.keys()].filter((c) => !order.includes(c)).sort() };
  }
  return { order, cycle: [] };
}

export function formatMoney(m: Money | null): string {
  if (!m) return "•••";
  const [int = "0", frac = ""] = m.amount.split(".");
  const neg = int.startsWith("-");
  const digits = neg ? int.slice(1) : int;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const cents = (frac + "00").slice(0, 2);
  return `${neg ? "-" : ""}$ ${grouped},${cents}`;
}
