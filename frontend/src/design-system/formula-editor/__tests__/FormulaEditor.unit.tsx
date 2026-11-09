// T-FORMULA-ENGINE-06 (P08, unit, gate G1): Editor shows positioned errors and runs test cases; accessible labels.
import { describe, expect, it } from "vitest";
import { useState } from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { renderDs } from "@app/test/render";
import { createApiClient } from "@lib/api/client";
import { FormulaEditor } from "../FormulaEditor";
import { createFixtureFormulaService, FIXTURE_LABEL } from "../fixtureService";
import { createApiFormulaService } from "../apiService";
import type { FormulaInputField, FormulaService, FormulaTestCase } from "../types";

const SCHEMA: FormulaInputField[] = [
  { name: "precio_base", type: "decimal", label: "Precio base" },
  { name: "descuento", type: "decimal", label: "Descuento %" },
];

function Harness({ initial, service, cases }: { initial: string; service: FormulaService; cases?: FormulaTestCase[] }) {
  const [expr, setExpr] = useState(initial);
  const [tc, setTc] = useState<FormulaTestCase[]>(cases ?? []);
  return <FormulaEditor value={expr} onChange={setExpr} schema={SCHEMA} service={service} testCases={tc} onTestCasesChange={setTc} serviceLabel={FIXTURE_LABEL} />;
}

describe("T-FORMULA-ENGINE-06 FormulaEditor", () => {
  it("T-FORMULA-ENGINE-06 shows positioned errors (line/column) and moves the caret to them", async () => {
    const user = userEvent.setup();
    //            0         1         2
    //            0123456789012345678901234567
    const expr = "precio_base * (1 - descuentos / 100)";
    renderDs(<Harness initial={expr} service={createFixtureFormulaService()} />);
    const textarea = screen.getByLabelText("Expresión de la fórmula");
    await user.click(screen.getByRole("button", { name: "Validar" }));

    const errButton = await screen.findByRole("button", { name: /Ir al error en la línea 1, columna 20/ });
    const li = errButton.closest("li") as HTMLElement;
    expect(li).toHaveAttribute("data-error-code", "formula_unknown_identifier");
    expect(li).toHaveAttribute("data-start", "19");
    expect(li).toHaveAttribute("data-end", "29");
    expect(errButton.textContent).toContain("Línea 1, columna 20");
    expect(textarea).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("1 error en la fórmula")).toBeInTheDocument();

    // The faulty token is marked in the highlight layer.
    const marked = document.querySelector(".nx-tok-error");
    expect(marked?.textContent).toBe("descuentos");

    // Activating the error moves focus and selection to the exact range.
    await user.click(errButton);
    expect(textarea).toHaveFocus();
    expect((textarea as HTMLTextAreaElement).selectionStart).toBe(19);
    expect((textarea as HTMLTextAreaElement).selectionEnd).toBe(29);

    // Multi-line position + syntax error.
    fireEvent.change(textarea, { target: { value: "precio_base *\n  (1 - descuento / 100" } });
    expect(screen.queryByRole("button", { name: /Ir al error/ })).toBeNull(); // stale positions hidden after editing
    await user.click(screen.getByRole("button", { name: "Validar" }));
    const err2 = await screen.findByRole("button", { name: /Ir al error en la línea 2/ });
    expect(err2.closest("li")).toHaveAttribute("data-error-code", "formula_syntax_error");

    // Fixing the expression clears the errors.
    fireEvent.change(textarea, { target: { value: "precio_base * (1 - descuento / 100)" } });
    await user.click(screen.getByRole("button", { name: "Validar" }));
    expect(await screen.findByText("La fórmula es válida.")).toBeInTheDocument();
    expect(textarea).not.toHaveAttribute("aria-invalid");
  });

  it("T-FORMULA-ENGINE-06 runs test cases and reports pass/fail with Decimal comparison", async () => {
    const user = userEvent.setup();
    const cases: FormulaTestCase[] = [
      { id: "a", inputs: { precio_base: "100000", descuento: "10" }, expected: "90000.00" },
      { id: "b", inputs: { precio_base: "0.1", descuento: "0" }, expected: "0.1" },
      { id: "c", inputs: { precio_base: "999999999999.9999", descuento: "50" }, expected: "1" },
    ];
    renderDs(<Harness initial="max(precio_base * (1 - descuento / 100), 0)" service={createFixtureFormulaService()} cases={cases} />);
    await user.click(screen.getByRole("button", { name: "Ejecutar casos de prueba" }));
    await waitFor(() => expect(document.querySelector("[data-nx-formula-summary]")?.textContent).toBe("2 de 3 casos correctos"));
    const row = (id: string) => document.querySelector(`[data-case="${id}"]`) as HTMLElement;
    expect(row("a")).toHaveAttribute("data-result", "pass");
    expect(row("b")).toHaveAttribute("data-result", "pass");
    expect(row("c")).toHaveAttribute("data-result", "fail");
    expect(within(row("c")).getByText("Falló: se obtuvo 499999999999.99995")).toBeInTheDocument();
    expect(within(row("a")).getByText("Explicación del cálculo")).toBeInTheDocument();

    // Editing a case and re-running updates the result; an evaluation error is shown per case.
    fireEvent.change(screen.getByLabelText("Resultado esperado del caso 3"), { target: { value: "499999999999.99995" } });
    fireEvent.change(screen.getByLabelText("Entrada precio_base del caso 2"), { target: { value: "abc" } });
    await user.click(screen.getByRole("button", { name: "Ejecutar casos de prueba" }));
    await waitFor(() => expect(row("c")).toHaveAttribute("data-result", "pass"));
    expect(row("b")).toHaveAttribute("data-result", "error");
    expect(row("b").textContent).toContain("debe ser un número decimal");

    // Add / remove cases.
    await user.click(screen.getByRole("button", { name: "Agregar caso" }));
    expect(screen.getByLabelText("Entrada descuento del caso 4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Eliminar caso 4" }));
    expect(screen.queryByLabelText("Entrada descuento del caso 4")).toBeNull();
  });

  it("T-FORMULA-ENGINE-06 maps server problem+json positions from the P07 API", async () => {
    const user = userEvent.setup();
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetcher = async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Fórmula inválida",
          status: 422,
          code: "formula_syntax_error",
          trace_id: "trace-1",
          errors: [{ pointer: "/expression/12-13", code: "formula_syntax_error", detail: "Paréntesis sin cerrar" }],
        }),
        { status: 422, headers: { "Content-Type": "application/problem+json" } },
      );
    };
    const service = createApiFormulaService(createApiClient(fetcher), "company-1");
    renderDs(<Harness initial="precio_base (" service={service} />);
    await user.click(screen.getByRole("button", { name: "Validar" }));
    const err = await screen.findByRole("button", { name: /Ir al error en la línea 1, columna 13/ });
    expect(err.textContent).toContain("Paréntesis sin cerrar");
    expect(calls[0]?.url).toBe("/api/v1/workflow/formulas:validate");
    const headers = calls[0]?.init?.headers as Record<string, string>;
    expect(headers["Idempotency-Key"]).toMatch(/[0-9a-f-]{36}/);
    expect(headers["X-Company-Id"]).toBe("company-1");
  });

  it("T-FORMULA-ENGINE-06 accessible labels: every control is named and axe reports no serious violations", async () => {
    const user = userEvent.setup();
    const cases: FormulaTestCase[] = [{ id: "a", inputs: { precio_base: "10", descuento: "0" }, expected: "10" }];
    const { container } = renderDs(<Harness initial="precio_base + x" service={createFixtureFormulaService()} cases={cases} />);
    await user.click(screen.getByRole("button", { name: "Validar" }));
    await screen.findByRole("button", { name: /Ir al error/ });

    const textarea = screen.getByRole("textbox", { name: "Expresión de la fórmula" });
    expect(textarea.getAttribute("aria-describedby")?.split(" ").length).toBeGreaterThanOrEqual(3);
    for (const id of textarea.getAttribute("aria-describedby")?.split(" ") ?? []) expect(document.getElementById(id)).not.toBeNull();
    for (const input of container.querySelectorAll("input, textarea, button")) {
      const name = input.getAttribute("aria-label") || input.textContent?.trim() || (input.id && container.querySelector(`label[for="${input.id}"]`)?.textContent);
      expect(name, input.outerHTML).toBeTruthy();
    }
    const res = await axe.run(container, { resultTypes: ["violations"], rules: { "color-contrast": { enabled: false } } });
    const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
  });
});
