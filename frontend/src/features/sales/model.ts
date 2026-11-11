// Pure view-model helpers of the sales screens (no I/O; unit-tested). Decimal strings only (no float, D-001).
import { Dec, formatCOP, sumDecimals } from "@lib/money";
import type { AgingBucket, AgingDto, OverrideStatus, PriceSnapshot, PriceSource, RepriceDiff, SalesDocumentDto } from "./api";

export const PRICE_SOURCE_LABEL: Record<PriceSource, string> = {
  OVERRIDE: "Precio manual",
  FORMULA: "Fórmula de precio",
  LIST: "Lista de precios",
  BASE: "Precio base",
};

export const OVERRIDE_LABEL: Record<OverrideStatus, string> = {
  NONE: "",
  WITHIN_LIMIT: "Dentro del límite de descuento",
  PENDING_APPROVAL: "Pendiente de aprobación",
  APPROVED: "Aprobado",
  REJECTED: "Rechazado",
};

export const DOCUMENT_STATUS_LABEL: Record<string, string> = {
  DRAFT: "Borrador",
  SENT: "Enviada",
  ACCEPTED: "Aceptada",
  ORDERED: "Convertida en pedido",
  PENDING_APPROVAL: "Pendiente de aprobación",
  CONFIRMED: "Confirmado",
  PARTIALLY_DELIVERED: "Entregado parcialmente",
  DELIVERED: "Entregado",
  INVOICED: "Facturado",
  POSTED: "Contabilizada",
  CANCELLED: "Anulado",
};

export const AGING_LABEL: Record<AgingBucket, string> = {
  current: "Por vencer",
  "1_30": "1–30 días",
  "31_60": "31–60 días",
  "61_90": "61–90 días",
  over_90: "Más de 90 días",
};

export interface ExplainRow {
  label: string;
  value: string;
}

function money(value: string | null | undefined, currency: string): string {
  if (value == null || value === "") return "—";
  return currency === "COP" ? formatCOP(value) : `${new Dec(value).toFixed()} ${currency}`;
}

/** Rows of the price-explain popover (D-024). Only what the server sent is shown: cost / trace appear only for
 * principals with the cost role, because the API strips them for everyone else. The formula text is never shown. */
export function explainRows(snap: PriceSnapshot): ExplainRow[] {
  const currency = snap.currency ?? "COP";
  const rows: ExplainRow[] = [];
  if (snap.source) rows.push({ label: "Origen del precio", value: PRICE_SOURCE_LABEL[snap.source] });
  if (snap.resolved_source && snap.source === "OVERRIDE")
    rows.push({ label: "Precio de referencia", value: `${money(snap.resolved_price, currency)} (${PRICE_SOURCE_LABEL[snap.resolved_source]})` });
  const detail = snap.detail ?? {};
  if (detail.price_list_code) rows.push({ label: "Lista", value: `${detail.price_list_code}${detail.min_qty ? ` · desde ${new Dec(detail.min_qty).toFixed()}` : ""}` });
  if (detail.formula_code) rows.push({ label: "Fórmula", value: `${detail.formula_code}${detail.formula_version ? ` v${detail.formula_version}` : ""}` });
  for (const [name, value] of Object.entries(detail.inputs ?? {})) rows.push({ label: `Entrada · ${name}`, value: String(value) });
  if (snap.override) {
    if (snap.override.effective_discount_pct)
      rows.push({ label: "Descuento efectivo", value: `${new Dec(snap.override.effective_discount_pct).toFixed()} % (límite ${new Dec(snap.override.limit_pct ?? "0").toFixed()} %)` });
    if (snap.override.reason) rows.push({ label: "Motivo", value: snap.override.reason });
  }
  if (snap.currency && snap.currency !== "COP" && snap.fx_rate) rows.push({ label: "TRM", value: `${new Dec(snap.fx_rate).toFixed()} (${snap.rate_date ?? ""})` });
  if (snap.rounding) rows.push({ label: "Redondeo", value: snap.rounding.replace("ROUND_HALF_UP", "Mitad hacia arriba").replace(":", " · decimales ") });
  if (snap.unit_price) rows.push({ label: "Precio unitario", value: money(snap.unit_price, currency) });
  return rows;
}

/** Summary of a draft reprice preview: changed lines and the net change of the document. */
export function repriceSummary(diff: RepriceDiff[]): { changed: number; delta: string } {
  const changed = diff.filter((d) => d.changed);
  return { changed: changed.length, delta: sumDecimals(changed.map((d) => d.delta_amount)) };
}

/** Posted documents are never repriced (D-024): the UI offers repricing only on drafts. */
export function canReprice(doc: Pick<SalesDocumentDto, "document_status">): boolean {
  return doc.document_status === "DRAFT";
}

export function pendingOverride(doc: Pick<SalesDocumentDto, "lines">): boolean {
  return doc.lines.some((l) => l.override_status === "PENDING_APPROVAL" || l.override_status === "REJECTED");
}

/** Overdue share of the aging (Σ buckets past due ÷ total) as a percentage string; "—" without a base. */
export function overdueShare(aging: Pick<AgingDto, "totals" | "total">): string {
  const total = new Dec(aging.total || "0");
  if (total.isZero()) return "—";
  const overdue = sumDecimals([aging.totals["1_30"], aging.totals["31_60"], aging.totals["61_90"], aging.totals.over_90]);
  return `${new Dec(overdue).div(total).times(100).toDecimalPlaces(1).toFixed(1)} %`;
}
