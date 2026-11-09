/**
 * Helpers for the shared FormulaEditor (P08 design-system) when used inside the
 * workflow module: positioned error messages in es-CO and the explain trace.
 */
import type { FormulaValidation, FormulaEvaluation } from "../api";

const MESSAGES: Record<string, string> = {
  formula_syntax_error: "Error de sintaxis",
  formula_unknown_identifier: "Identificador desconocido",
  formula_type_error: "Error de tipos",
  formula_budget_exceeded: "La fórmula excede los límites permitidos",
  formula_dependency_cycle: "Ciclo de dependencias",
  formula_test_case_failed: "Falló un caso de prueba",
  formula_division_by_zero: "División por cero",
  formula_not_approved: "Fórmula no aprobada",
};

export function describeErrors(v: FormulaValidation): string[] {
  return v.errors.map((e) => {
    const where = e.line !== undefined && e.column !== undefined ? ` (línea ${e.line}, columna ${e.column})` : "";
    return `${MESSAGES[e.code] ?? e.code}${where}: ${e.detail}`;
  });
}

export function traceRows(ev: FormulaEvaluation): { expr: string; value: string }[] {
  return ev.trace.map((t) => ({ expr: t.expr, value: t.value }));
}
