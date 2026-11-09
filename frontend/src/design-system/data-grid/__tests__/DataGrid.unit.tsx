// T-P08-02 (keyboard-only grid) + DataGrid features: 100k rows virtualized, field masks remove columns, inline edit
// validation (Enter/Esc), selection + bulk action, Decimal-safe totals.
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderDs } from "@app/test/render";
import { DataGrid, type GridColumn } from "../DataGrid";

interface Row {
  id: string;
  numero: string;
  tercero: string;
  total: string;
  costo: string;
}

const COLS: GridColumn<Row>[] = [
  { id: "numero", header: "Número", editable: true, validate: (v) => (/^FV-\d+$/.test(v) ? null : "Use el formato FV-123") },
  { id: "tercero", header: "Tercero" },
  { id: "total", header: "Total", type: "COP", total: true },
  { id: "costo", header: "Costo", type: "COP", total: true },
];

const rows = (n: number): Row[] =>
  Array.from({ length: n }, (_, i) => ({ id: `r${i}`, numero: `FV-${i + 1}`, tercero: `Tercero sintético ${i % 50}`, total: i % 2 ? "0.1" : "0.2", costo: "1" }));

const grid = () => screen.getByRole("grid", { name: "Facturas" });
const active = () => document.getElementById(grid().getAttribute("aria-activedescendant") ?? "") as HTMLElement;

describe("T-P08-02 DataGrid", () => {
  it("T-P08-02 virtualizes 100,000 rows and totals with Decimal math", () => {
    renderDs(<DataGrid label="Facturas" resource="sales.invoice" rows={rows(100_000)} columns={COLS} />);
    expect(grid()).toHaveAttribute("aria-rowcount", String(100_000 + 2));
    expect(within(grid()).getAllByRole("row").length).toBeLessThan(50);
    const totals = document.querySelector("[data-nx-totals]") as HTMLElement;
    // 50,000 × 0.2 + 50,000 × 0.1 = 15,000 exactly (floats would drift)
    expect(totals.textContent).toContain("$ 15.000");
    expect(totals.textContent).toContain("$ 100.000");
  });

  it("T-P08-02 keyboard-only navigation, selection, inline edit with validation", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const bulk = vi.fn();
    renderDs(<DataGrid label="Facturas" resource="sales.invoice" rows={rows(200)} columns={COLS} onEdit={onEdit} bulkActions={[{ id: "anular", label: "Anular seleccionadas", run: bulk }]} />);
    grid().focus();
    expect(active()).toHaveAttribute("aria-colindex", "1");
    await user.keyboard("{ArrowRight}");
    expect(active().textContent).toBe("FV-1");
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(active().textContent).toBe("FV-3");
    await user.keyboard("{End}");
    expect(active()).toHaveAttribute("aria-colindex", "5");
    await user.keyboard("{Control>}{End}{/Control}");
    expect(active().closest('[role="row"]')).toHaveAttribute("aria-rowindex", "201");
    await user.keyboard("{Control>}{Home}{/Control}");
    expect(active().closest('[role="row"]')).toHaveAttribute("aria-rowindex", "2");

    // Space on the selection column selects; bulk toolbar appears.
    await user.keyboard(" ");
    expect(screen.getByText("1 fila seleccionada")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Anular seleccionadas" }));
    expect(bulk).toHaveBeenCalledWith([expect.objectContaining({ id: "r0" })]);

    // Enter edits, invalid value is rejected with a message, Esc cancels, valid value commits.
    grid().focus();
    await user.keyboard("{ArrowRight}{Enter}");
    const input = screen.getByRole("textbox", { name: "Número" });
    expect(input).toHaveFocus();
    await user.clear(input);
    await user.type(input, "XX{Enter}");
    expect(screen.getByText("Valor no válido: Use el formato FV-123")).toHaveAttribute("role", "alert");
    expect(onEdit).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Número" })).toBeNull();
    expect(grid()).toHaveFocus();
    await user.keyboard("{Enter}");
    const again = screen.getByRole("textbox", { name: "Número" });
    await user.clear(again);
    await user.type(again, "FV-999{Enter}");
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: "r0" }), "numero", "FV-999");
  });

  it("T-P08-02 masked fields are absent from columns and filters", async () => {
    const user = userEvent.setup();
    renderDs(<DataGrid label="Facturas" resource="sales.invoice" rows={rows(5)} columns={COLS} masks={{ "sales.invoice": ["costo"] }} />);
    expect(within(grid()).queryByText("Costo")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Filtros" }));
    expect(screen.queryByLabelText("Costo")).toBeNull();
    expect(screen.getByLabelText("Tercero")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Tercero"), "sintético 3");
    expect(screen.getByText("1 registro")).toBeInTheDocument();
  });
});
