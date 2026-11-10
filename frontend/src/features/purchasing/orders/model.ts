// Order / receipt labels and progress text (pure; Decimal strings via decimal.js, never float).
import { Dec } from "@lib/money";
import type { OrderDto } from "../api";

export const ORDER_STATUS_LABEL: Record<string, string> = {
  DRAFT: "Borrador",
  SUBMITTED: "Por aprobar",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  SENT: "Enviada",
  ACKNOWLEDGED: "Confirmada por el proveedor",
  PARTIALLY_RECEIVED: "Recibida parcialmente",
  RECEIVED: "Recibida",
  CLOSED: "Cerrada",
  CANCELLED: "Anulada",
};

/** "60 % recibido · 0 % facturado" over the ordered base quantity of all lines. */
export function progress(o: OrderDto): string {
  let ordered = new Dec(0);
  let received = new Dec(0);
  let invoiced = new Dec(0);
  for (const l of o.lines) {
    ordered = ordered.plus(l.quantity.value);
    received = received.plus(l.received_qty);
    invoiced = invoiced.plus(l.invoiced_qty);
  }
  if (ordered.isZero()) return "—";
  const pct = (v: InstanceType<typeof Dec>) => v.div(ordered).times(100).toDecimalPlaces(0).toString();
  return `${pct(received)} % recibido · ${pct(invoiced)} % facturado`;
}
