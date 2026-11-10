// FormulaEditor contract (owner P08; F-FORMULA-ENGINE, consumers P10 pricing, P13 payroll, P07 approvals).
// Evaluation always happens on the server (P07 sandboxed engine). The client only tokenizes for highlighting.
export type FormulaFieldType = "decimal" | "integer" | "string" | "boolean" | "date";

export interface FormulaInputField {
  name: string;
  type: FormulaFieldType;
  label?: string;
}

/** Positioned error; offsets are 0-based UTF-16 indexes into the expression, `end` exclusive. */
export interface FormulaError {
  code: string; // stable code, e.g. formula_syntax_error, formula_unknown_identifier
  message: string;
  start: number;
  end: number;
}

export interface ValidationResult {
  ok: boolean;
  errors: FormulaError[];
}

export interface ExplainStep {
  expression: string;
  value: string;
}

export interface SimulationResult {
  ok: boolean;
  result: string | null;
  explain: ExplainStep[];
  error?: FormulaError | null;
}

export interface FormulaTestCase {
  id: string;
  inputs: Record<string, string>;
  expected: string;
}

export interface FormulaService {
  validate(expression: string, schema: FormulaInputField[], signal?: AbortSignal): Promise<ValidationResult>;
  simulate(expression: string, schema: FormulaInputField[], inputs: Record<string, string>, signal?: AbortSignal): Promise<SimulationResult>;
}
