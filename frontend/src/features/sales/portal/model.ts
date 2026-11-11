// Pure view model of the customer portal (F-PORTAL-CUS): labels, exact decimal sums, payment and request validation.
import type { CustomerOpenItem, CustomerPaymentIntent, Money, RequestInput, RequestType } from "./api";

export const FIXTURE_NOTICE = "Datos sintéticos (DEV). Los documentos descargados son SINTÉTICOS — sin validez fiscal.";
export const PAYMENT_NOTICE = "Pago con proveedor SIMULADO (sin cobro real). Tesorería confirma el pago y emite el recibo de caja.";

export const DOC_STATUS_LABEL: Record<string, string> = {
  SUBMITTED: "Enviada",
  APPROVED: "Aprobada",
  POSTED: "Contabilizada",
  CANCELLED: "Anulada",
  DRAFT: "Borrador",
};
export const QUOTE_STATUS_LABEL: Record<string, string> = { SENT: "Por aceptar", ACCEPTED: "Aceptada", ORDERED: "Convertida en pedido", CANCELLED: "Anulada" };
export const INTENT_STATUS_LABEL: Record<CustomerPaymentIntent["intent_status"], string> = {
  CREATED: "Creada",
  SUCCEEDED_SIMULATED: "Pago simulado exitoso — pendiente de tesorería",
  FAILED: "Rechazada",
  CONFIRMED_BY_TREASURY: "Confirmada por tesorería",
};
export const REQUEST_STATUS_LABEL: Record<string, string> = {
  SUBMITTED: "Recibida",
  QUOTED: "Cotizada",
  ORDERED: "Pedido creado",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  RESOLVED: "Atendida",
};
export const REQUEST_TYPE_LABEL: Record<RequestType, string> = { QUOTE: "Solicitud de cotización", PROFILE_CHANGE: "Cambio de datos", GENERAL: "Consulta general" };
/** Party fields a customer may ask to change (the server applies them only after an internal approval). */
export const PROFILE_FIELDS: Record<string, string> = {
  email: "Correo de contacto",
  einvoice_email: "Correo para factura electrónica",
  phone: "Teléfono",
  address: "Dirección",
};

const AMOUNT = /^\d{1,16}(\.\d{1,2})?$/;
const QTY = /^\d{1,14}(\.\d{1,6})?$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Exact decimal sum of money strings (no floating point): returns a string with `places` decimals. */
export function sumMoney(values: string[], places = 2): string {
  const scale = 10n ** BigInt(places);
  let total = 0n;
  for (const raw of values) {
    const neg = raw.trim().startsWith("-");
    const [int, frac = ""] = raw.trim().replace(/^-/, "").split(".");
    const cents = BigInt(int || "0") * scale + BigInt((frac + "0".repeat(places)).slice(0, places) || "0");
    total += neg ? -cents : cents;
  }
  const sign = total < 0n ? "-" : "";
  const abs = total < 0n ? -total : total;
  const i = abs / scale;
  const f = (abs % scale).toString().padStart(places, "0");
  return places ? `${sign}${i}.${f}` : `${sign}${i}`;
}

export function formatMoney(m: Money | null): string {
  if (!m) return "—";
  const n = Number(m.amount);
  return Number.isFinite(n) ? `${n.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${m.currency}` : `${m.amount} ${m.currency}`;
}

/** Payment intent body from the selected open invoices; the amount must be > 0 and ≤ their open balance. */
export function paymentIntent(selected: CustomerOpenItem[], amount: string): { body: { documents: string[]; amount: Money } | null; error: string | null } {
  if (selected.length === 0) return { body: null, error: "Seleccione al menos una factura." };
  const currencies = new Set(selected.map((s) => s.balance.currency));
  if (currencies.size !== 1) return { body: null, error: "Las facturas deben estar en la misma moneda." };
  const value = amount.trim();
  if (!AMOUNT.test(value) || sumMoney([value]) === "0.00") return { body: null, error: "Monto en pesos mayor que cero, máximo 2 decimales." };
  const open = sumMoney(selected.map((s) => s.balance.amount));
  if (compare(value, open) > 0) return { body: null, error: `El monto supera el saldo pendiente (${open}).` };
  return { body: { documents: selected.map((s) => s.document.id), amount: { amount: value, currency: selected[0]!.balance.currency } }, error: null };
}

function compare(a: string, b: string): number {
  const d = sumMoney([a, `-${b.replace(/^-/, "")}`]);
  return d.startsWith("-") ? -1 : d === "0.00" ? 0 : 1;
}

/** Request body (quote: item ids + quantities, no prices; profile: allow-listed fields only). */
export function requestBody(
  type: RequestType,
  message: string,
  items: Array<{ item_id: string; quantity: string }>,
  changes: Record<string, string>,
): { body: RequestInput | null; error: string | null } {
  if (type === "QUOTE") {
    const rows = items.filter((i) => i.item_id.trim() || i.quantity.trim());
    if (rows.length === 0) return { body: null, error: "Agregue al menos un producto." };
    for (const r of rows) {
      if (!UUID.test(r.item_id.trim())) return { body: null, error: "Código de producto inválido." };
      if (!QTY.test(r.quantity.trim()) || Number(r.quantity) <= 0) return { body: null, error: "Cantidad mayor que cero, máximo 6 decimales." };
    }
    return { body: { request_type: type, message: message.trim(), items: rows.map((r) => ({ item_id: r.item_id.trim(), quantity: r.quantity.trim() })) }, error: null };
  }
  if (type === "PROFILE_CHANGE") {
    const filled = Object.fromEntries(Object.entries(changes).filter(([k, v]) => k in PROFILE_FIELDS && v.trim()).map(([k, v]) => [k, v.trim()]));
    if (Object.keys(filled).length === 0) return { body: null, error: "Indique al menos un dato a cambiar." };
    return { body: { request_type: type, message: message.trim(), changes: filled }, error: null };
  }
  if (!message.trim()) return { body: null, error: "Escriba su consulta." };
  return { body: { request_type: type, message: message.trim() }, error: null };
}

export function canAccept(quoteStatus: string): boolean {
  return quoteStatus === "SENT";
}

export function canSimulate(intent: CustomerPaymentIntent): boolean {
  return intent.intent_status === "CREATED";
}

/** Keys an internal-only document could carry; the portal UI never renders them (defence in depth for T-PORTAL-CUS-04). */
export const INTERNAL_KEYS = ["cost", "cost_total", "cost_value", "margin", "price_snapshot", "price_source", "formula", "formula_id", "trace", "salesperson_id", "credit_limit"] as const;

export function visibleColumns<T extends object>(row: T): string[] {
  return Object.keys(row).filter((k) => !(INTERNAL_KEYS as readonly string[]).includes(k));
}
