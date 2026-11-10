// FormulaService backed by the P07 API (POST /api/v1/workflow/formulas:validate and :simulate, F-FORMULA-ENGINE).
// Error positions: problem.errors[].pointer "/expression/<start>[-<end>]" (requested in contracts/change-requests/P08-1.md);
// a result object { errors:[{code,message,start,end}] } is also accepted.
import { ApiError, type ApiClient } from "@lib/api/client";
import type { FormulaError, FormulaInputField, FormulaService, SimulationResult, ValidationResult } from "./types";

function fromPointer(p: { pointer?: string; code?: string; detail?: string }, fallbackLen: number): FormulaError {
  const m = /\/expression\/(\d+)(?:-(\d+))?/.exec(p.pointer ?? "");
  const start = m ? Number.parseInt(m[1] ?? "0", 10) : 0;
  const end = m && m[2] ? Number.parseInt(m[2], 10) : m ? start + 1 : fallbackLen;
  return { code: p.code ?? "formula_syntax_error", message: p.detail ?? p.code ?? "", start, end };
}

export function createApiFormulaService(api: ApiClient, companyId: string | null): FormulaService {
  const headers = companyId ? { "X-Company-Id": companyId } : undefined;
  return {
    async validate(expression, schema: FormulaInputField[], signal): Promise<ValidationResult> {
      try {
        const res = await api.post<{ result?: { errors?: FormulaError[] } }>("/api/v1/workflow/formulas:validate", {
          body: { params: { expression, input_schema: schema } },
          signal,
          headers,
        });
        const errors = res?.result?.errors ?? [];
        return { ok: errors.length === 0, errors };
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && e.problem) {
          const list = e.problem.errors?.length ? e.problem.errors : [{ code: e.problem.code, detail: e.problem.detail }];
          return { ok: false, errors: list.map((p) => fromPointer(p, expression.length)) };
        }
        throw e;
      }
    },
    async simulate(expression, schema, inputs, signal): Promise<SimulationResult> {
      try {
        const res = await api.post<{ result?: { value?: string; explain?: SimulationResult["explain"] } }>("/api/v1/workflow/formulas:simulate", {
          body: { params: { expression, input_schema: schema, inputs } },
          signal,
          headers,
        });
        return { ok: true, result: res?.result?.value ?? null, explain: res?.result?.explain ?? [] };
      } catch (e) {
        if (e instanceof ApiError && e.status === 422 && e.problem) {
          const p = e.problem.errors?.[0] ?? { code: e.problem.code, detail: e.problem.detail };
          return { ok: false, result: null, explain: [], error: fromPointer(p, expression.length) };
        }
        throw e;
      }
    },
  };
}
