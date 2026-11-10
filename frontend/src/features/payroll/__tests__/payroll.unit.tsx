// F-PAY-FORMULA UI (P13b): NOT LEGALLY VERIFIED labels on the payslip and the concepts page, masked
// amounts, the evaluation-order view and the contributed tab.
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { renderDs } from "@app/test/render";

import { ConceptsPage } from "../ConceptsPage";
import { PayslipExplain } from "../PayslipExplain";
import { PAYROLL_CONCEPTS_TAB, registerPayrollContribution, visiblePayrollTabs, type ContributedTab } from "../contribution";
import { NOT_LEGALLY_VERIFIED, dependencyOrder, formatMoney, legalLabel, type Payslip, type PayrollConcept } from "../model";

const FIXTURE_PARAM = {
  code: "ss.salud.empleado",
  input: "param_salud_empleado",
  value: "0.04",
  status: "VERIFIED-PRIMARY",
  source_id: "S-36",
  legally_verified: false,
  label: NOT_LEGALLY_VERIFIED,
};

function payslip(over: Partial<Payslip> = {}): Payslip {
  return {
    id: "ps-1",
    run_id: "run-1",
    status: "POSTED",
    employee_id: "emp-1",
    employee_name: "Empleado Sintético",
    earnings: { amount: "2349095.0000", currency: "COP" },
    deductions: { amount: "204000.0000", currency: "COP" },
    net_pay: { amount: "2145095.0000", currency: "COP" },
    employer_cost: { amount: "240000.0000", currency: "COP" },
    lines: [
      {
        concept: "SALUD_EMP",
        concept_type: "DEDUCCION",
        sequence: 7,
        amount: { amount: "84000.0000", currency: "COP" },
        formula_version: 1,
        formula_hash: "h",
        inputs: { param_salud_empleado: "4.00" },
        trace: [{ expr: "SALARIO + BONO", value: "2100000" }],
        parameters: [FIXTURE_PARAM],
      },
    ],
    legally_verified: false,
    legal_notice: NOT_LEGALLY_VERIFIED,
    parameters: [FIXTURE_PARAM],
    masked: [],
    ...over,
  };
}

function concept(code: string, depends_on: string[] = [], over: Partial<PayrollConcept> = {}): PayrollConcept {
  return {
    id: `id-${code}`,
    code,
    name: code,
    status: "ACTIVE",
    concept_type: "DEVENGO",
    formula_id: "f",
    depends_on,
    account_role: "",
    salary_base: false,
    electronic_payroll_code: null,
    version: 1,
    ...over,
  };
}

function fakeFetch(routes: Record<string, unknown>): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(routes).find((k) => url.includes(k));
    return new Response(JSON.stringify(key ? routes[key] : {}), { status: key ? 200 : 404, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
}

describe("T-PAY-FORMULA-03 fixture parameters labelled NOT LEGALLY VERIFIED in the UI", () => {
  it("labels the payslip and every fixture parameter of a line", () => {
    renderDs(<PayslipExplain payslip={payslip()} />);
    const status = screen.getByRole("status", { name: "Estado legal del desprendible" });
    expect(status).toHaveTextContent(NOT_LEGALLY_VERIFIED);
    const row = screen.getByRole("row", { name: /SALUD_EMP/ });
    expect(within(row).getByText(NOT_LEGALLY_VERIFIED)).toBeInTheDocument();
    expect(within(row).getByRole("list", { name: "Traza de SALUD_EMP" })).toHaveTextContent("SALARIO + BONO = 2100000");
  });

  it("drops the label only for reviewed, non-synthetic values", () => {
    const reviewed = { ...FIXTURE_PARAM, legally_verified: true, label: "" };
    renderDs(<PayslipExplain payslip={payslip({ legally_verified: true, legal_notice: "", parameters: [reviewed], lines: [] })} />);
    expect(screen.queryByText(NOT_LEGALLY_VERIFIED)).toBeNull();
    expect(legalLabel(FIXTURE_PARAM)).toBe(NOT_LEGALLY_VERIFIED);
    expect(legalLabel({ legally_verified: false })).toBe(NOT_LEGALLY_VERIFIED);
  });

  it("labels the legal parameters on the concepts page", async () => {
    const fetchImpl = fakeFetch({
      "concepts:validate-graph": { status: "valid", data: { on: "2026-01-31", order: ["SALARIO", "SALUD_EMP"], errors: [], parameters: [FIXTURE_PARAM] } },
      "concepts?": { items: [concept("SALUD_EMP", ["SALARIO"], { concept_type: "DEDUCCION" }), concept("SALARIO")], next_cursor: null },
    });
    renderDs(<ConceptsPage companyId="c1" onDate="2026-01-31" fetchImpl={fetchImpl} />);
    await waitFor(() => expect(screen.getByText(/param_salud_empleado/)).toBeInTheDocument());
    const item = screen.getByText(/param_salud_empleado/).closest("li")!;
    expect(within(item).getByText(NOT_LEGALLY_VERIFIED)).toBeInTheDocument();
  });

  it("shows masked amounts as ••• and never a number", () => {
    renderDs(
      <PayslipExplain
        payslip={payslip({ earnings: null, deductions: null, net_pay: null, employer_cost: null, lines: null, masked: ["net_pay"] })}
      />,
    );
    expect(screen.queryByText(/2\.145\.095/)).toBeNull();
    expect(screen.getAllByText("•••").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByRole("status", { name: "Estado legal del desprendible" })).toHaveTextContent(NOT_LEGALLY_VERIFIED);
  });
});

describe("T-PAY-FORMULA-01 evaluation order view", () => {
  it("lists concepts in the server's topological order and flags cycles locally", async () => {
    expect(dependencyOrder([concept("AHORRO", ["SALARIO"]), concept("SALARIO")]).order).toEqual(["SALARIO", "AHORRO"]);
    expect(dependencyOrder([concept("A", ["B"]), concept("B", ["A"])])).toEqual({ order: null, cycle: ["A", "B"] });
    const fetchImpl = fakeFetch({
      "concepts:validate-graph": { status: "valid", data: { on: "2026-01-31", order: ["SALARIO", "AHORRO"], errors: [], parameters: [] } },
      "concepts?": { items: [concept("AHORRO", ["SALARIO"], { concept_type: "DEDUCCION" }), concept("SALARIO")], next_cursor: null },
    });
    renderDs(<ConceptsPage companyId="c1" onDate="2026-01-31" fetchImpl={fetchImpl} />);
    await waitFor(() => expect(document.querySelectorAll("tr[data-concept]").length).toBe(2));
    const codes = [...document.querySelectorAll("tr[data-concept]")].map((r) => r.getAttribute("data-concept"));
    expect(codes).toEqual(["SALARIO", "AHORRO"]);
  });

  it("shows the server's cycle error", async () => {
    const fetchImpl = fakeFetch({
      "concepts:validate-graph": {
        status: "invalid",
        data: { on: "2026-01-31", order: [], errors: [{ code: "formula_dependency_cycle", detail: "A → B → A" }], parameters: [] },
      },
      "concepts?": { items: [], next_cursor: null },
    });
    renderDs(<ConceptsPage companyId="c1" onDate="2026-01-31" fetchImpl={fetchImpl} />);
    await waitFor(() => expect(screen.getByRole("alert", { name: "Errores del grafo de conceptos" })).toHaveTextContent("formula_dependency_cycle"));
  });
});

describe("P13b payroll contribution", () => {
  it("contributes the Conceptos y fórmulas tab only with payroll.concept.read", () => {
    expect(visiblePayrollTabs(new Set())).toEqual([]);
    expect(visiblePayrollTabs(new Set(["payroll.concept.read"]))).toEqual([PAYROLL_CONCEPTS_TAB]);
    const seen: [string, string, ContributedTab][] = [];
    registerPayrollContribution({ contributeTab: (m, s, t) => seen.push([m, s, t]) });
    expect(seen).toEqual([["talento", "nomina", PAYROLL_CONCEPTS_TAB]]);
    expect(formatMoney({ amount: "2145095.5000", currency: "COP" })).toBe("$ 2.145.095,50");
  });
});
