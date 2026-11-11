// Payroll helpers of the P22c feature specs (T-PAY-FORMULA-*). DEV/test only; synthetic values only.
import type { Locator } from "@playwright/test";
import type { App } from "./stack";

export const PAYROLL = "/api/v1/payroll";
export const WORKFLOW = "/api/v1/workflow";

/** User-defined earning: a company-policy 5 % of SALARIO (user data, not a legal value). 5 % of 3.000.000 = 150.000. */
export const NEW_CONCEPT = {
  code: "BONO_ALIM_SINT",
  name: "Bono de alimentación (SINTÉTICO)",
  expression: "round(percent(SALARIO, 5), 2, 'HALF_UP')",
  expected: 150000,
};

export function conceptRow(table: Locator, code: string): Locator {
  return table.locator(`tbody tr[data-concept="${code}"]`);
}

/** Formula (P07, domain payroll) authored by `nomina`, approved by `jefe_nomina` (SoD), then the payroll concept. */
export async function createConceptViaApi(app: App): Promise<{ formula: any; concept: any }> {
  const author = app.api("nomina");
  const approver = app.api("jefe_nomina");
  const base = { salary: "3000000", days_worked: "30", period_days: "30", novelty_amount: "0" };
  const formula = await author.post(`${WORKFLOW}/formulas`, {
    domain: "payroll",
    code: NEW_CONCEPT.code,
    expression: NEW_CONCEPT.expression,
    inputs: {},
    result_type: "money",
    depends_on: ["SALARIO"],
    tests: [{ inputs: base, expected: String(NEW_CONCEPT.expected) }],
    output_places: 2,
    output_rounding: "HALF_UP",
    effective_from: "2026-01-01",
  });
  await author.post(`${WORKFLOW}/formulas/${formula.id}:submit`, {});
  await approver.post(`${WORKFLOW}/formulas/${formula.id}:approve`, { reason: "revisión de nómina (sintético)" });
  const concept = await author.post(`${PAYROLL}/concepts`, {
    code: NEW_CONCEPT.code,
    name: NEW_CONCEPT.name,
    concept_type: "DEVENGO",
    formula_id: formula.id,
    depends_on: ["SALARIO"],
  });
  return { formula, concept };
}

export function conceptsTable(page: import("@playwright/test").Page): Locator {
  return page.getByRole("main").getByRole("table", { name: "Conceptos en orden de evaluación" });
}

/** Each [before, after] pair: `before` is listed (evaluated) before `after`. */
export async function expectConceptOrder(table: Locator, pairs: [string, string][]): Promise<void> {
  const { expect } = await import("@playwright/test");
  const order = await table.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-concept")));
  for (const [before, after] of pairs) {
    expect(order.indexOf(before), `${before} listed`).toBeGreaterThanOrEqual(0);
    expect(order.indexOf(after), `${after} evaluated after ${before}`).toBeGreaterThan(order.indexOf(before));
  }
}
