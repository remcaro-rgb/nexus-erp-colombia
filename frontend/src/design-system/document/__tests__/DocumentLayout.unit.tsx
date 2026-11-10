// T-P08-02 document pattern: exactly one primary action, keyboard-reachable side panels, state badge.
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderDs } from "@app/test/render";
import { DocumentLayout, assertSinglePrimary } from "../DocumentLayout";
import { PostingBadge } from "../../components/Badge";

describe("T-P08-02 DocumentLayout", () => {
  it("T-P08-02 one primary action, keyboard side panel tabs, state badge", async () => {
    const user = userEvent.setup();
    const post = vi.fn();
    renderDs(
      <DocumentLayout
        title="Factura de venta"
        number="FV-1"
        state={<PostingBadge status="draft" />}
        header={<p>Encabezado</p>}
        lines={<p>Líneas del documento</p>}
        totals={<p>Totales del documento</p>}
        panels={{ accounting: <p>Vista contable simulada</p>, audit: <p>Historial de auditoría</p> }}
        actions={[
          { id: "save", label: "Guardar borrador", onClick: vi.fn() },
          { id: "post", label: "Contabilizar", onClick: post, primary: true },
        ]}
      />,
    );
    const bar = screen.getByRole("toolbar", { name: "Acciones del documento" });
    const buttons = within(bar).getAllByRole("button");
    expect(buttons.filter((b) => b.hasAttribute("data-primary"))).toHaveLength(1);
    expect(buttons.at(-1)?.textContent).toBe("Contabilizar");
    expect(screen.getByText("Borrador")).toBeInTheDocument();
    const tab = screen.getByRole("tab", { name: "Vista previa contable" });
    tab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Auditoría" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Historial de auditoría")).toBeInTheDocument();
    await user.tab();
    await user.tab();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(post).toHaveBeenCalledOnce();
    expect(() => assertSinglePrimary([{ id: "a", label: "A", onClick: () => undefined }])).toThrow(/exactly one primary/);
  });
});
