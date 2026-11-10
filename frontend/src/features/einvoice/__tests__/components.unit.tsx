import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderDs } from "@app/test/render";
import { DocumentsTable, ProvidersPanel } from "../components";
import type { FiscalDocument } from "../model";

const doc = (status: FiscalDocument["status"]): FiscalDocument => ({
  id: `d-${status}`, status, source: { type: "sales.invoice", id: "s", number: `SETP-${status}` }, document_type: "FE",
  fiscal_status: "SENT", cufe: "a".repeat(96), synthetic: true, version: 1,
});

describe("facturacion components", () => {
  it("shows actions only with their permission and the honest empty state", async () => {
    const onRetry = vi.fn();
    const { rerender } = renderDs(
      <DocumentsTable docs={[doc("error_retryable"), doc("accepted")]} canUpdate canExport={false} onRetry={onRetry} onResend={vi.fn()} xmlHref={(id) => `/x/${id}`} caption="Documentos" />,
    );
    expect(screen.getByText("Error — se reintentará · Simulado")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(onRetry).toHaveBeenCalledWith("d-error_retryable");
    expect(screen.getByRole("button", { name: "Reenviar correo" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "XML" })).toBeNull();
    rerender(<DocumentsTable docs={[doc("error_retryable")]} canUpdate={false} canExport onRetry={onRetry} onResend={vi.fn()} xmlHref={(id) => `/x/${id}`} caption="Documentos" />);
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    rerender(<DocumentsTable docs={[]} canUpdate canExport onRetry={onRetry} onResend={vi.fn()} xmlHref={(id) => id} caption="Documentos" />);
    expect(screen.getByRole("link", { name: "Ir a Facturas de venta" })).toHaveAttribute("href", "/m/ventas/facturas");
  });

  it("T-PT-OPEN-02 the provider form refuses a raw credential and shows the SIMULADO badge", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    renderDs(
      <ProvidersPanel
        providers={[{ id: "p1", code: "PT1", name: "PT sintético", status: "ACTIVE", adapter: "http_generic", base_url: "https://pt.invalid", environment: "SANDBOX", version: 1 }]}
        reports={{ p1: { adapter: "http_generic", suite_version: "pt-contract-1", passed: false, cases: [{ case: "status_poll", passed: false, detail: "" }] } }}
        canCreate canAdmin canExport exportHref="/export" onCreate={onCreate} onTest={vi.fn()}
      />,
    );
    expect(screen.getByText("SIMULADO — no certificado")).toBeInTheDocument();
    expect(screen.getByText(/Prueba de contrato fallida: status_poll/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Código"), "PT2");
    await user.type(screen.getByLabelText("Nombre"), "Otro");
    await user.type(screen.getByLabelText(/Referencia de credenciales/), "my-raw-token-1234");
    await user.click(screen.getByRole("button", { name: "Registrar proveedor" }));
    expect(onCreate).not.toHaveBeenCalled();
    expect(screen.getByText(/Use una referencia secret:\/\/einvoice\//)).toBeInTheDocument();
    await user.clear(screen.getByLabelText(/Referencia de credenciales/));
    await user.type(screen.getByLabelText(/Referencia de credenciales/), "secret://einvoice/pt2");
    await user.click(screen.getByRole("button", { name: "Registrar proveedor" }));
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ code: "PT2", credentials_ref: "secret://einvoice/pt2" }));
  });
});
