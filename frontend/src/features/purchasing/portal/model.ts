// Pure view model of the supplier portal (F-PORTAL-SUP): labels, quote form validation and the bank-change rule.
import type { SupplierOrder, SupplierRfq } from "./api";

export const ORDER_STATUS_LABEL: Record<string, string> = {
  SENT: "Por confirmar",
  ACKNOWLEDGED: "Confirmada",
  CHANGE_REQUESTED: "Cambios solicitados",
  PARTIALLY_RECEIVED: "Recibida parcialmente",
  RECEIVED: "Recibida",
  CLOSED: "Cerrada",
  CANCELLED: "Anulada",
};

export const PAYMENT_STATUS_LABEL: Record<string, string> = { OPEN: "Por pagar", PARTIAL: "Pago parcial", PAID: "Pagada" };

export const FIXTURE_NOTICE = "Datos sintéticos (DEV). Las cifras no son información real de ningún proveedor.";

const PRICE = /^\d{1,14}(\.\d{1,4})?$/;

/** Quote lines from the price inputs (one per RFQ line) — errors keyed by line number, es-CO messages. */
export function quoteLines(rfq: SupplierRfq, prices: Record<number, string>): { lines: unknown[]; errors: Record<number, string> } {
  const errors: Record<number, string> = {};
  const lines = rfq.lines.map((ln) => {
    const price = (prices[ln.line_no] ?? "").trim();
    if (!PRICE.test(price)) errors[ln.line_no] = "Precio unitario en pesos, máximo 4 decimales.";
    return { line_no: ln.line_no, quantity: ln.quantity, unit_price: { amount: price, currency: rfq.currency } };
  });
  return { lines, errors };
}

export function canQuote(rfq: SupplierRfq): boolean {
  return rfq.rfq_status === "OPEN";
}

export function canAnswer(order: SupplierOrder): boolean {
  return order.status === "SENT";
}

/** A bank-account change needs two internal approvals (shown to the supplier before submitting). */
export function approvalsNeeded(parameters: Record<string, unknown>): number {
  return "bank_account" in parameters ? 2 : 1;
}

/** Base64 of a file's bytes (browser FileReader result "data:…;base64,XXXX"). */
export function stripDataUrl(dataUrl: string): string {
  const i = dataUrl.indexOf("base64,");
  return i >= 0 ? dataUrl.slice(i + 7) : dataUrl;
}
