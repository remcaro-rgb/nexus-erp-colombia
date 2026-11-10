// FIXTURE FormulaService (owner P08) — FOR TESTS AND OFFLINE DEMOS ONLY, labelled "Simulado".
// The real engine is P07's sandboxed server evaluator; this double implements a tiny Decimal subset
// (+ - * / % comparisons, parentheses, unary minus, min/max/abs/round/if) so the editor can be exercised
// without the backend. It never uses eval/Function and never runs user text as code.
import { Dec } from "@lib/money";
import { tokenize, type Token } from "./tokenize";
import type { FormulaError, FormulaInputField, FormulaService, SimulationResult, ValidationResult } from "./types";

export const FIXTURE_LABEL = "Simulado";

class FormulaFail extends Error {
  constructor(public readonly err: FormulaError) {
    super(err.message);
  }
}

type Val = InstanceType<typeof Dec> | boolean;

function parser(src: string, fields: Map<string, FormulaInputField>, inputs: Record<string, string> | null) {
  const toks = tokenize(src).filter((t) => t.kind !== "space");
  let p = 0;
  const explain: Array<{ expression: string; value: string }> = [];
  const peek = (): Token | undefined => toks[p];
  const fail = (code: string, message: string, t?: Token): never => {
    const at = t ?? toks[toks.length - 1];
    throw new FormulaFail({ code, message, start: at ? at.start : 0, end: at ? at.end : Math.max(1, src.length) });
  };
  const expect = (text: string) => {
    const t = peek();
    if (!t || t.text !== text) fail("formula_syntax_error", t ? `Se esperaba "${text}" y se encontró "${t.text}"` : `Falta "${text}"`, t);
    p++;
  };
  const num = (v: Val, t?: Token) => (typeof v === "boolean" ? fail("formula_type_error", "Se esperaba un número", t) : v);

  function primary(): Val {
    const t = peek();
    if (!t) return fail("formula_syntax_error", "La expresión está incompleta");
    if (t.kind === "number") {
      p++;
      if (!/^\d+(\.\d+)?$/.test(t.text)) fail("formula_syntax_error", `Número no válido "${t.text}"`, t);
      return new Dec(t.text);
    }
    if (t.text === "(") {
      p++;
      const v = comparison();
      expect(")");
      return v;
    }
    if (t.text === "-") {
      p++;
      return num(primary(), t).neg();
    }
    if (t.kind === "func") {
      p++;
      expect("(");
      const args: Val[] = [];
      if (peek()?.text !== ")") {
        args.push(comparison());
        while (peek()?.text === ",") {
          p++;
          args.push(comparison());
        }
      }
      expect(")");
      const name = t.text.toLowerCase();
      const n = (i: number) => num(args[i] ?? fail("formula_syntax_error", `Faltan argumentos en ${name}`, t), t);
      let out: Val;
      switch (name) {
        case "min":
          out = Dec.min(...args.map((_, i) => n(i)));
          break;
        case "max":
          out = Dec.max(...args.map((_, i) => n(i)));
          break;
        case "abs":
          out = n(0).abs();
          break;
        case "round":
          out = n(0).toDecimalPlaces(args.length > 1 ? n(1).toNumber() : 0, Dec.ROUND_HALF_UP);
          break;
        case "if":
          if (typeof args[0] !== "boolean") fail("formula_type_error", "La condición de if debe ser verdadera o falsa", t);
          out = args[0] ? (args[1] ?? fail("formula_syntax_error", "Faltan argumentos en if", t)) : (args[2] ?? fail("formula_syntax_error", "Faltan argumentos en if", t));
          break;
        default:
          return fail("formula_unknown_identifier", `La función "${t.text}" no está disponible en la simulación`, t);
      }
      explain.push({ expression: src.slice(t.start, toks[p - 1]?.end ?? t.end), value: String(out) });
      return out;
    }
    if (t.kind === "ident") {
      p++;
      const f = fields.get(t.text);
      if (!f) fail("formula_unknown_identifier", `"${t.text}" no es un campo de entrada declarado`, t);
      if (!inputs) return new Dec(0);
      const raw = inputs[t.text];
      if (raw === undefined || raw === "") fail("formula_syntax_error", `Falta el valor de "${t.text}"`, t);
      if (!/^-?\d+(\.\d+)?$/.test(raw as string)) fail("formula_type_error", `"${t.text}" debe ser un número decimal`, t);
      return new Dec(raw as string);
    }
    return fail("formula_syntax_error", `Símbolo inesperado "${t.text}"`, t);
  }
  function term(): Val {
    let v = primary();
    while (peek() && ["*", "/", "%"].includes(peek()?.text ?? "")) {
      const op = peek() as Token;
      p++;
      const r = num(primary(), op);
      const l = num(v, op);
      if ((op.text === "/" || op.text === "%") && r.isZero()) fail("formula_division_by_zero", "División por cero", op);
      v = op.text === "*" ? l.mul(r) : op.text === "/" ? l.div(r) : l.mod(r);
    }
    return v;
  }
  function sum(): Val {
    let v = term();
    while (peek() && ["+", "-"].includes(peek()?.text ?? "")) {
      const op = peek() as Token;
      p++;
      const r = num(term(), op);
      v = op.text === "+" ? num(v, op).plus(r) : num(v, op).minus(r);
    }
    return v;
  }
  function comparison(): Val {
    const l = sum();
    const op = peek();
    if (op && op.kind === "op" && ["<", ">", "<=", ">=", "==", "!="].includes(op.text)) {
      p++;
      const a = num(l, op);
      const b = num(sum(), op);
      switch (op.text) {
        case "<":
          return a.lt(b);
        case ">":
          return a.gt(b);
        case "<=":
          return a.lte(b);
        case ">=":
          return a.gte(b);
        case "==":
          return a.eq(b);
        default:
          return !a.eq(b);
      }
    }
    return l;
  }
  return {
    run(): { value: Val; explain: typeof explain } {
      if (toks.length === 0) fail("formula_syntax_error", "La expresión está vacía");
      for (const t of toks) if (t.kind === "unknown") fail("formula_syntax_error", `Carácter no permitido "${t.text}"`, t);
      const value = comparison();
      if (p < toks.length) fail("formula_syntax_error", `Símbolo inesperado "${toks[p]?.text}"`, toks[p]);
      return { value, explain };
    },
  };
}

export function createFixtureFormulaService(): FormulaService {
  return {
    async validate(expression: string, schema: FormulaInputField[]): Promise<ValidationResult> {
      const fields = new Map(schema.map((f) => [f.name, f]));
      const errors: FormulaError[] = [];
      // Collect every unknown identifier first (several positioned errors), then the first syntax error.
      for (const t of tokenize(expression)) {
        if (t.kind === "ident" && !fields.has(t.text)) {
          errors.push({ code: "formula_unknown_identifier", message: `"${t.text}" no es un campo de entrada declarado`, start: t.start, end: t.end });
        }
      }
      if (errors.length === 0) {
        try {
          parser(expression, fields, null).run();
        } catch (e) {
          if (e instanceof FormulaFail) errors.push(e.err);
          else throw e;
        }
      }
      return { ok: errors.length === 0, errors };
    },
    async simulate(expression, schema, inputs): Promise<SimulationResult> {
      try {
        const { value, explain } = parser(expression, new Map(schema.map((f) => [f.name, f])), inputs).run();
        return { ok: true, result: typeof value === "boolean" ? String(value) : value.toFixed(), explain };
      } catch (e) {
        if (e instanceof FormulaFail) return { ok: false, result: null, explain: [], error: e.err };
        throw e;
      }
    },
  };
}
