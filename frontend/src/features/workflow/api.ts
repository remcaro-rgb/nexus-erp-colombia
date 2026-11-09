/**
 * Typed client of the workflow API (contracts/openapi/modules/workflow.yaml).
 * Uses only the public API; every mutation sends an Idempotency-Key.
 * Decimals travel as strings (never JSON numbers).
 */

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  errors?: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(public readonly problem: Problem) {
    super(problem.detail);
  }
}

export interface FormulaTraceEntry {
  expr: string;
  value: string;
  pos: number;
}

export interface FormulaEvaluation {
  code: string;
  version: number;
  hash: string;
  inputs: Record<string, string>;
  tables: { name: string; version: number }[];
  trace: FormulaTraceEntry[];
  result: string;
}

export interface FormulaValidation {
  ok: boolean;
  errors: { code: string; detail: string; position?: number; line?: number; column?: number }[];
  tests: { name: string; passed: boolean; got: string | null; expected: string | null; error: string | null }[];
  order: string[];
  hash: string | null;
}

export interface FormulaDraft {
  code: string;
  domain: "approval" | "pricing" | "payroll";
  expression: string;
  result_type: "decimal" | "money" | "quantity" | "percent" | "boolean" | "date";
  inputs: Record<string, unknown>;
  tests?: { cases: { inputs: Record<string, string>; expected: string }[] };
  depends_on?: string[];
}

export interface KpiValue {
  value: number | string | null;
  previous?: number | string | null;
  unit: string;
  empty_reason?: string | null;
}

export interface AprobacionesKpis {
  module: "aprobaciones";
  company_id: string;
  period: string;
  kpis: Partial<Record<"pendientes" | "vencidas_sla" | "tiempo_aprobacion" | "eventos_auditoria", KpiValue>>;
  panels: { actividad: { id: string; at: string; action: string; resource_type: string; drill: string }[] };
}

export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export class WorkflowApi {
  constructor(
    private readonly fetcher: Fetcher,
    private readonly companyId: () => string | null,
  ) {}

  private headers(mutating: boolean): Record<string, string> {
    const h: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
    const company = this.companyId();
    if (company) h["X-Company-Id"] = company;
    if (mutating) h["Idempotency-Key"] = newIdempotencyKey();
    return h;
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await this.fetcher(path, {
      method,
      headers: this.headers(method !== "GET"),
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
    const data = res.status === 204 ? null : await res.json();
    if (!res.ok) throw new ApiError(data as Problem);
    return data as T;
  }

  validateFormula(draft: FormulaDraft) {
    return this.call<{ status: "VALID" | "INVALID"; data: FormulaValidation }>("POST", "/api/v1/workflow/formulas:validate", {
      parameters: draft,
    });
  }

  simulateFormula(draft: FormulaDraft, values: Record<string, string>) {
    return this.call<{ status: "SIMULATED"; data: FormulaEvaluation }>("POST", "/api/v1/workflow/formulas:simulate", {
      parameters: { ...draft, values },
    });
  }

  inbox(params: { vencidas?: boolean } = {}) {
    const q = params.vencidas ? "?vencidas=1" : "";
    return this.call<{ items: unknown[]; next_cursor: string | null }>("GET", `/api/v1/workflow/approvals${q}`);
  }

  decide(id: string, approve: boolean, reason: string) {
    return this.call("POST", `/api/v1/workflow/approvals/${id}:${approve ? "approve" : "reject"}`, { reason });
  }

  kpis(period?: string) {
    const q = period ? `?period=${encodeURIComponent(period)}` : "";
    return this.call<AprobacionesKpis>("GET", `/api/v1/kpis/aprobaciones${q}`);
  }

  notifications(unreadOnly = false) {
    return this.call<{ items: unknown[]; unread_count: number }>(
      "GET",
      `/api/v1/workflow/notifications${unreadOnly ? "?unread=1" : ""}`,
    );
  }
}
