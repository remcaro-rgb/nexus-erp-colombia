// T-PAY-FORMULA-05 (e2e, gate G3): UI: create concept with formula, simulate, run payroll, see explain on payslip.
// World: tests/cross_module/test_xm_e2e_payroll.py::world_pay_formula (real P04 policy + local OIDC, real P07 formula
// lifecycle, real P06 parameter seed 2026 = NOT LEGALLY VERIFIED, real P13a HR directory, P13b payroll services).
// Amounts are synthetic (contract salary 3.000.000 COP; the new concept is a company-policy 5 % of SALARIO, user data).
// Talento has no shell descriptor: the runner uses `augment` for "nomina/conceptos" (wiring defect filed separately).
import { expect, expectLabelledControls, tabTo, test } from "./support/stack";
import { NEW_CONCEPT, PAYROLL, conceptRow, conceptsTable, createConceptViaApi, expectConceptOrder } from "./support/payroll";

const PAGE_BROKEN =
  "Conceptos y fórmulas does not load in the shell: ConceptsPage is not a shell PageComponent (requires companyId/onDate props the shell never passes, no default export) and payroll/api.ts calls /api/v1/payroll/* without the bearer token";

test("T-PAY-FORMULA-05 concept with formula, simulate, run payroll, explain on payslip (keyboard + labels)", async ({ app }) => {
  const { page } = app;
  const d = app.world.data;
  const main = page.getByRole("main");
  const api = app.api("nomina");

  // 1. Talento › Nómina: keyboard from the Liquidación tab to "Conceptos y fórmulas" (ARIA tablist, deep link)
  await app.open("/m/talento/nomina/liquidacion");
  const tablist = page.getByRole("tablist", { name: "Nómina" });
  await expect(tablist).toBeVisible();
  await tabTo(page, tablist.getByRole("tab", { selected: true }));
  await page.keyboard.press("ArrowRight");
  await expect(tablist.getByRole("tab", { name: "Conceptos y fórmulas", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/\/m\/talento\/nomina\/conceptos$/);
  await expectLabelledControls(page);

  // the concept list in evaluation order (D-025) with the legal parameters it reads, labelled NOT LEGALLY VERIFIED
  const table = conceptsTable(page);
  const listed = await conceptRow(table, "SALARIO").waitFor({ timeout: 15_000 }).then(() => true, () => false);
  if (listed) {
    await expect(main.getByRole("heading", { name: "Conceptos y fórmulas de nómina" })).toBeVisible();
    await expect(conceptRow(table, "SALARIO")).toContainText("Devengo");
    await expectConceptOrder(table, [["SALARIO", "AHORRO"], ["BONO", "SALUD_EMP"]]);
    await expect(main.locator('[data-parameter="ss.salud.empleado"]')).toContainText("NOT LEGALLY VERIFIED");
    await expect(main.getByRole("alert")).toHaveCount(0);
  } else {
    await app.gap("DEF-P22-064", PAGE_BROKEN, async () => {
      // evidence: the exact request payroll/api.ts makes from the page (same-origin fetch, no Authorization header)
      const status = await page.evaluate(
        async (cid) =>
          (await fetch("/api/v1/payroll/concepts?limit=500", { credentials: "same-origin", headers: { Accept: "application/json", "X-Company-Id": cid } })).status,
        d.company_id as string,
      );
      expect(status, "payroll/api.ts request without bearer").toBe(401);
      const concepts = await api.get(`${PAYROLL}/concepts`, { limit: "500" });
      expect(concepts.items.map((c: any) => c.code)).toContain("SALARIO");
      const graph = await api.post(`${PAYROLL}/concepts:validate-graph`, { parameters: { on: "2026-10-31" } });
      expect(graph.status).toBe("valid");
      expect(graph.data.order.indexOf("AHORRO")).toBeGreaterThan(graph.data.order.indexOf("SALARIO"));
      const salud = graph.data.parameters.find((p: any) => p.code === "ss.salud.empleado");
      expect(salud.legally_verified).toBe(false);
      expect(salud.label).toContain("NOT LEGALLY VERIFIED");
    });
  }

  // 2. create a user-defined earning with a formula (domain payroll; the approver is another user, D-023) and its concept
  await expect(main.getByRole("button", { name: /nuevo|crear|agregar/i })).toHaveCount(0);
  await app.gap(
    "DEF-P22-060",
    "Conceptos y fórmulas offers no create/edit action: no concept form and no formula editor (design-system FormulaEditor is mounted by no payroll page)",
    () => createConceptViaApi(app),
  );
  await page.reload();
  const shown = await conceptRow(table, NEW_CONCEPT.code).waitFor({ timeout: 15_000 }).then(() => true, () => false);
  if (shown) {
    const row = conceptRow(table, NEW_CONCEPT.code);
    await expect(row).toContainText(NEW_CONCEPT.name);
    await expect(row).toContainText("Devengo");
    await expect(row.getByRole("cell").nth(4)).toHaveText("SALARIO");
    await expect(row.getByRole("cell").nth(5)).toHaveText("Fórmula");
    await expectConceptOrder(table, [["SALARIO", NEW_CONCEPT.code]]);
  } else {
    await app.gap("DEF-P22-064", PAGE_BROKEN, async () => {
      const graph = await api.post(`${PAYROLL}/concepts:validate-graph`, { parameters: { on: "2026-10-31" } });
      expect(graph.status).toBe("valid");
      expect(graph.data.order.indexOf(NEW_CONCEPT.code)).toBeGreaterThan(graph.data.order.indexOf("SALARIO"));
    });
  }

  // 3. simulate the period for the synthetic employee: would-be payslip, nothing persisted
  const run = await api.post(`${PAYROLL}/runs`, { period_id: d.period_id });
  const sim = await app.gap(
    "DEF-P22-061",
    "no simulate action in Talento › Nómina (payrollApi.simulateRun exists in payroll/api.ts, no page calls it)",
    () => api.post(`${PAYROLL}/runs/${run.id}:simulate`, {}),
  );
  expect(sim.persisted).toBe(false);
  expect(sim.graph_order.indexOf(NEW_CONCEPT.code)).toBeGreaterThan(sim.graph_order.indexOf("SALARIO"));
  const simDoc = sim.documents.find((x: any) => x.employee_id === d.employee_id);
  expect(simDoc, "simulation has a document for the synthetic employee").toBeTruthy();
  expect(Number(simDoc.lines.find((l: any) => l.concept === "SALARIO").amount)).toBe(Number(d.salary));
  expect(Number(simDoc.lines.find((l: any) => l.concept === NEW_CONCEPT.code).amount)).toBe(NEW_CONCEPT.expected);
  expect(simDoc.legal_notice).toBe("NOT LEGALLY VERIFIED");
  expect((await api.get(`${PAYROLL}/payslips`, { run_id: run.id })).items).toHaveLength(0);

  // 4. run payroll in Nómina › Liquidación: calculate (payroll admin), approve (another user, D-023)
  await app.open("/m/talento/nomina/liquidacion");
  await expect(main.getByText("Módulo aún no disponible")).toBeVisible();
  await expect(main.getByRole("button", { name: /calcular|aprobar|liquidar/i })).toHaveCount(0);
  await app.gap(
    "DEF-P22-062",
    "Talento › Nómina › Liquidación has no page (the shell shows 'Módulo aún no disponible'): no UI to create, calculate or approve a payroll run",
    async () => {
      const calc = await api.post(`${PAYROLL}/runs/${run.id}:calculate`, {});
      expect(calc.data.status).toBe("CALCULATED");
      expect(calc.data.legal_notice).toBe("NOT LEGALLY VERIFIED");
      const approved = await app.api("jefe_nomina").post(`${PAYROLL}/runs/${run.id}:approve`, {});
      expect(approved.data.status).toBe("APPROVED");
    },
  );

  // 5. the payslip with its explain: per line the formula version, the trace and the labelled legal parameters
  const slips = await api.get(`${PAYROLL}/payslips`, { run_id: run.id });
  expect(slips.items).toHaveLength(1);
  const slip = await app.gap(
    "DEF-P22-063",
    "PayslipExplain (frontend/src/features/payroll/PayslipExplain.tsx) is unreachable: no payslip list or route in Talento › Nómina renders it",
    () => api.get(`${PAYROLL}/payslips/${slips.items[0].id}`),
  );
  expect(slip.status).toBe("CALCULATED");
  expect(slip.legal_notice).toBe("NOT LEGALLY VERIFIED");
  // DEF-P22-065: "payroll.payslip" has no field catalog in the P04 registry, so the real policy masks every field for everyone
  expect(slip.masked, "DEF-P22-065: payroll admin (payroll.payslip.read, restricted-pii) reads the payslip amounts and explain").toEqual([]);
  expect(slip.employee_name).toBe(d.employee_name);
  const line = slip.lines.find((l: any) => l.concept === NEW_CONCEPT.code);
  expect(Number(line.amount.amount)).toBe(NEW_CONCEPT.expected);
  expect(line.formula_version).toBeGreaterThanOrEqual(1);
  expect(line.formula_hash).toBeTruthy();
  expect(JSON.stringify(line.trace)).toContain("SALARIO");
  const salud = slip.lines.find((l: any) => l.concept === "SALUD_EMP");
  expect(salud.parameters.map((p: any) => p.code)).toContain("ss.salud.empleado");
  expect(salud.parameters.every((p: any) => p.legally_verified === false)).toBe(true);
});
