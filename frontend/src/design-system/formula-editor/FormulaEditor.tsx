// FormulaEditor (owner P08; F-FORMULA-ENGINE UI: syntax highlighting, inline errors with position, input schema panel,
// test-case table, simulate with explain trace). Used by Ventas › Fórmulas de precio and Talento › Conceptos y fórmulas.
// Accessibility: labelled textarea + help text; errors are listed as buttons that move the caret to the error position;
// results are announced in polite live regions; every test-case input has an explicit label.
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, Play, Plus, Trash2, XCircle } from "lucide-react";
import { cn } from "@lib/cn";
import { decimalEquals } from "@lib/money";
import { useT } from "@i18n/index";
import { Button, IconButton } from "../components/Button";
import { inputClass } from "../components/Field";
import { lineCol, tokenize } from "./tokenize";
import type { FormulaError, FormulaInputField, FormulaService, FormulaTestCase, SimulationResult } from "./types";

export interface FormulaEditorProps {
  value: string;
  onChange: (expression: string) => void;
  schema: FormulaInputField[];
  service: FormulaService;
  testCases?: FormulaTestCase[];
  onTestCasesChange?: (cases: FormulaTestCase[]) => void;
  /** Validate automatically (debounced) while typing. */
  autoValidate?: boolean;
  readOnly?: boolean;
  label?: string;
  /** Shown next to results when the service is a fixture (e.g. "Simulado"). */
  serviceLabel?: string;
}

interface CaseResult {
  status: "pass" | "fail" | "error";
  actual: string | null;
  message?: string;
  explain: SimulationResult["explain"];
}

let caseSeq = 0;
const newCaseId = () => `c${Date.now().toString(36)}${(caseSeq++).toString(36)}`;

export function FormulaEditor({
  value,
  onChange,
  schema,
  service,
  testCases: casesProp,
  onTestCasesChange,
  autoValidate = false,
  readOnly,
  label,
  serviceLabel,
}: FormulaEditorProps) {
  const t = useT();
  const uid = useId().replace(/:/g, "");
  const area = useRef<HTMLTextAreaElement>(null);
  const layer = useRef<HTMLPreElement>(null);
  // Errors are kept with the exact text they were computed for; editing the text hides stale positions.
  const [validation, setValidation] = useState<{ text: string; errors: FormulaError[] } | null>(null);
  const [validating, setValidating] = useState(false);
  const [casesState, setCasesState] = useState<FormulaTestCase[]>(casesProp ?? []);
  const cases = casesProp ?? casesState;
  const setCases = (next: FormulaTestCase[]) => {
    setCasesState(next);
    onTestCasesChange?.(next);
  };
  const [results, setResults] = useState<Record<string, CaseResult>>({});
  const [running, setRunning] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  const validate = useCallback(async () => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setValidating(true);
    try {
      const res = await service.validate(value, schema, c.signal);
      if (!c.signal.aborted) setValidation({ text: value, errors: res.errors });
      return res;
    } finally {
      if (!c.signal.aborted) setValidating(false);
    }
  }, [service, value, schema]);

  useEffect(() => {
    if (!autoValidate) return;
    const h = window.setTimeout(() => void validate(), 400);
    return () => window.clearTimeout(h);
  }, [autoValidate, validate]);

  useEffect(() => () => ctrl.current?.abort(), []);

  const currentErrors = validation && validation.text === value ? validation.errors : null;

  const tokens = useMemo(() => tokenize(value), [value]);
  const errorAt = (start: number, end: number) => (currentErrors ?? []).some((e) => start < Math.max(e.end, e.start + 1) && end > e.start);

  const goTo = (e: FormulaError) => {
    const el = area.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(e.start, Math.max(e.start, e.end));
  };

  async function runTests() {
    setRunning(true);
    const out: Record<string, CaseResult> = {};
    try {
      for (const tc of cases) {
        try {
          const r = await service.simulate(value, schema, tc.inputs);
          if (!r.ok || r.result === null) {
            out[tc.id] = { status: "error", actual: null, message: r.error?.message ?? "", explain: r.explain };
          } else {
            const pass = decimalEquals(r.result, tc.expected) || r.result === tc.expected;
            out[tc.id] = { status: pass ? "pass" : "fail", actual: r.result, explain: r.explain };
          }
        } catch (e) {
          out[tc.id] = { status: "error", actual: null, message: e instanceof Error ? e.message : String(e), explain: [] };
        }
      }
    } finally {
      setResults(out);
      setRunning(false);
    }
  }

  const passed = Object.values(results).filter((r) => r.status === "pass").length;
  const ran = Object.keys(results).length;
  const helpId = `${uid}-help`;
  const errListId = `${uid}-errors`;
  const inputsId = `${uid}-inputs`;

  return (
    <div className="flex flex-col gap-4" data-nx-component="formula-editor">
      <div className="grid gap-4 lg:grid-cols-[1fr_240px]">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${uid}-expr`} className="text-xs font-semibold text-secondary">
            {label ?? t("formula.label")}
          </label>
          <p id={helpId} className="text-xs text-muted">
            {t("formula.help")}
          </p>
          <div className="relative rounded-control border border-line bg-input">
            <pre ref={layer} aria-hidden="true" className="nx-formula-layer pointer-events-none absolute inset-0 m-0 overflow-hidden text-body">
              {tokens.map((tk) => (
                <span key={tk.start} className={cn(tk.kind !== "space" && `nx-tok-${tk.kind === "paren" || tk.kind === "comma" ? "op" : tk.kind}`, errorAt(tk.start, tk.end) && "nx-tok-error")}>
                  {tk.text}
                </span>
              ))}
              {"\n"}
            </pre>
            <textarea
              ref={area}
              id={`${uid}-expr`}
              value={value}
              readOnly={readOnly}
              spellCheck={false}
              autoCapitalize="off"
              autoComplete="off"
              rows={4}
              aria-describedby={[helpId, currentErrors?.length ? errListId : null, inputsId].filter(Boolean).join(" ")}
              aria-invalid={currentErrors && currentErrors.length > 0 ? true : undefined}
              onChange={(e) => onChange(e.target.value)}
              onScroll={(e) => {
                if (layer.current) layer.current.scrollTop = e.currentTarget.scrollTop;
              }}
              className="nx-formula-input relative block w-full resize-y bg-transparent text-transparent caret-[var(--nx-color-text-primary)] outline-none selection:bg-indigo-100 selection:text-slate-900"
            />
          </div>
          <div className="mt-1 flex items-center gap-2">
            <Button onClick={() => void validate()} loading={validating} variant="secondary">
              {validating ? t("formula.validating") : t("formula.validate")}
            </Button>
            {serviceLabel ? <span className="text-xs font-semibold text-warning">{serviceLabel}</span> : null}
          </div>
          <div role="status" aria-live="polite" className="text-sm">
            {currentErrors && currentErrors.length === 0 ? (
              <p className="flex items-center gap-1 text-positive">
                <CheckCircle2 aria-hidden="true" size={14} />
                {t("formula.valid")}
              </p>
            ) : null}
            {currentErrors && currentErrors.length > 0 ? <p className="font-semibold text-danger">{t("formula.errors", { count: currentErrors.length })}</p> : null}
          </div>
          {currentErrors && currentErrors.length > 0 ? (
            <ul id={errListId} className="flex flex-col gap-1" aria-label={t("formula.errors", { count: currentErrors.length })}>
              {currentErrors.map((e, i) => {
                const { line, col } = lineCol(value, e.start);
                return (
                  <li key={`${e.code}-${e.start}-${i}`} data-error-code={e.code} data-start={e.start} data-end={e.end}>
                    <button
                      type="button"
                      onClick={() => goTo(e)}
                      aria-label={`${t("formula.goToError", { line, col })}. ${e.message}`}
                      className="flex min-h-6 items-start gap-1 text-left text-xs text-danger hover:underline"
                    >
                      <XCircle aria-hidden="true" size={12} className="mt-0.5 shrink-0" />
                      <span>{t("formula.errorAt", { line, col, message: e.message })}</span>
                      <code className="ml-1 text-muted">{e.code}</code>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
        <section id={inputsId} aria-labelledby={`${uid}-inputs-h`} className="rounded-card border border-line p-3">
          <h4 id={`${uid}-inputs-h`} className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
            {t("formula.inputs")}
          </h4>
          <ul className="flex flex-col gap-1 text-sm">
            {schema.map((f) => (
              <li key={f.name} className="flex items-center justify-between gap-2">
                <code className="font-mono text-xs text-fg">{f.name}</code>
                <span className="text-xs text-muted">{f.label ?? f.type}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section aria-labelledby={`${uid}-tests-h`} className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <h4 id={`${uid}-tests-h`} className="text-sm font-bold text-fg">
            {t("formula.tests")}
          </h4>
          <div className="flex gap-2">
            {!readOnly ? (
              <Button
                icon={<Plus size={14} />}
                onClick={() => setCases([...cases, { id: newCaseId(), inputs: Object.fromEntries(schema.map((f) => [f.name, ""])), expected: "" }])}
              >
                {t("formula.addTest")}
              </Button>
            ) : null}
            <Button variant="primary" icon={<Play size={14} />} loading={running} disabled={cases.length === 0} onClick={() => void runTests()}>
              {running ? t("formula.running") : t("formula.runTests")}
            </Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="nx-sr-only">{t("formula.tests")}</caption>
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th scope="col" className="px-2 py-1">
                  #
                </th>
                {schema.map((f) => (
                  <th key={f.name} scope="col" className="px-2 py-1 font-mono">
                    {f.name}
                  </th>
                ))}
                <th scope="col" className="px-2 py-1">
                  {t("formula.expected")}
                </th>
                <th scope="col" className="px-2 py-1">
                  {t("formula.testResult")}
                </th>
                <th scope="col" className="px-2 py-1">
                  <span className="nx-sr-only">{t("doc.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {cases.map((tc, i) => {
                const n = i + 1;
                const r = results[tc.id];
                return (
                  <tr key={tc.id} className="border-b border-line-subtle align-top" data-case={tc.id} data-result={r?.status ?? "pending"}>
                    <th scope="row" className="px-2 py-1 text-xs font-semibold text-secondary">
                      {t("formula.case", { n })}
                    </th>
                    {schema.map((f) => (
                      <td key={f.name} className="px-2 py-1">
                        <input
                          aria-label={t("formula.testInput", { field: f.name, n })}
                          className={cn(inputClass, "min-w-24 font-mono")}
                          value={tc.inputs[f.name] ?? ""}
                          readOnly={readOnly}
                          onChange={(e) => setCases(cases.map((c) => (c.id === tc.id ? { ...c, inputs: { ...c.inputs, [f.name]: e.target.value } } : c)))}
                        />
                      </td>
                    ))}
                    <td className="px-2 py-1">
                      <input
                        aria-label={t("formula.testExpected", { n })}
                        className={cn(inputClass, "min-w-24 font-mono")}
                        value={tc.expected}
                        readOnly={readOnly}
                        onChange={(e) => setCases(cases.map((c) => (c.id === tc.id ? { ...c, expected: e.target.value } : c)))}
                      />
                    </td>
                    <td className="px-2 py-1 text-xs">
                      {r ? (
                        <div>
                          <p className={cn("font-semibold", r.status === "pass" ? "text-positive" : "text-danger")}>
                            {r.status === "pass"
                              ? t("formula.testPass")
                              : r.status === "fail"
                                ? t("formula.testFail", { actual: r.actual ?? "" })
                                : t("formula.testError", { message: r.message ?? "" })}
                          </p>
                          {r.explain.length ? (
                            <details>
                              <summary className="cursor-pointer text-muted">{t("formula.explain")}</summary>
                              <ol className="mt-1 list-decimal pl-4 font-mono">
                                {r.explain.map((s, k) => (
                                  <li key={k}>
                                    {s.expression} = {s.value}
                                  </li>
                                ))}
                              </ol>
                            </details>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-2 py-1">
                      {!readOnly ? (
                        <IconButton label={t("formula.removeTest", { n })} icon={<Trash2 size={14} />} onClick={() => setCases(cases.filter((c) => c.id !== tc.id))} />
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p role="status" aria-live="polite" data-nx-formula-summary="" className="text-sm font-semibold text-secondary">
          {ran ? t("formula.summary", { passed, total: ran }) : ""}
        </p>
      </section>
    </div>
  );
}
