// Pure view model of Compras › Solicitudes internas (F-INV-REQ): labels, validation, sourcing and lineage summaries.
import type { ConsolidationPreviewLine, LineStatus, NewRequisition, RequisitionDto, RequisitionStatus } from "./api";

export const STATUS_LABEL: Record<RequisitionStatus, string> = {
  DRAFT: "Borrador",
  SUBMITTED: "Enviada a aprobación",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  IN_PROGRESS: "En abastecimiento",
  FULFILLED: "Cumplida",
  PARTIALLY_FULFILLED_CLOSED: "Cerrada con cumplimiento parcial",
  CANCELLED: "Anulada",
};

export const LINE_STATUS_LABEL: Record<LineStatus, string> = {
  OPEN: "Abierta",
  RESERVED: "Reservada (traslado en borrador)",
  IN_TRANSFER: "En traslado",
  TO_PROCURE: "Por comprar",
  IN_PURCHASE: "En compra",
  FULFILLED: "Cumplida",
  CANCELLED: "Anulada",
};

export const ACTION_LABEL: Record<ConsolidationPreviewLine["action"], string> = {
  DRAFT_PO: "Borrador de orden de compra (requiere aprobación)",
  BUYER_TASK: "Tarea para el comprador",
};

export const REASON_LABEL: Record<string, string> = {
  auto_po_limit_exceeded: "Supera el límite de orden automática (D-011)",
  vendor_price_missing: "Sin precio vigente de proveedor (≤ 90 días)",
};

const DECIMAL = /^\d{1,14}(\.\d{1,6})?$/;

export interface DraftForm {
  warehouseId: string;
  neededBy: string;
  priority: NewRequisition["priority"];
  notes: string;
  lines: Array<{ itemId: string; qty: string; uom: string }>;
}

/** Field errors (es-CO) of the request form; an empty object means valid. */
export function validateDraft(form: DraftForm, today: string): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.warehouseId) errors.warehouseId = "Seleccione la bodega que solicita.";
  if (!form.neededBy) errors.neededBy = "Indique la fecha en que se necesita.";
  else if (form.neededBy < today) errors.neededBy = "La fecha no puede ser anterior a hoy.";
  if (!form.lines.length) errors.lines = "Agregue al menos una línea.";
  form.lines.forEach((ln, i) => {
    if (!ln.itemId) errors[`lines.${i}.itemId`] = "Seleccione el producto.";
    if (!DECIMAL.test(ln.qty) || Number(ln.qty) <= 0) errors[`lines.${i}.qty`] = "Cantidad positiva, máximo 6 decimales.";
  });
  return errors;
}

export function toRequest(form: DraftForm, today: string): NewRequisition {
  return {
    document_date: today,
    requesting_warehouse_id: form.warehouseId,
    needed_by: form.neededBy,
    priority: form.priority,
    notes: form.notes || undefined,
    lines: form.lines.map((ln, i) => ({ line_no: i + 1, item_id: ln.itemId, quantity: { value: ln.qty, uom: ln.uom || "UND" } })),
  };
}

/** "Traslado 6 · Compra 14" — the split of one requisition's lines (exact decimal strings, no float). */
export function sourcingSummary(req: RequisitionDto): { transfer: string; purchase: string; fulfilled: string } {
  const sum = (key: "qty_transfer" | "qty_procure" | "fulfilled_qty") => addDecimals(req.lines.map((l) => l[key]));
  return { transfer: sum("qty_transfer"), purchase: sum("qty_procure"), fulfilled: sum("fulfilled_qty") };
}

/** Exact sum of non-negative decimal strings (scaled bigint arithmetic; 6 decimals). */
export function addDecimals(values: string[]): string {
  const scale = 1_000_000n;
  let total = 0n;
  for (const v of values) {
    const [int, frac = ""] = v.split(".");
    total += BigInt(int || "0") * scale + BigInt((frac + "000000").slice(0, 6));
  }
  const int = total / scale;
  const frac = (total % scale).toString().padStart(6, "0").replace(/0+$/, "");
  return frac ? `${int}.${frac}` : `${int}`;
}

/** Σ sources = consolidated quantity (D-031), checked again in the UI before showing a run as consistent. */
export function lineageBalanced(line: ConsolidationPreviewLine): boolean {
  return addDecimals(line.sources.map((s) => s.qty)) === addDecimals([line.quantity]);
}

export function canDecide(req: RequisitionDto, userId: string | null): boolean {
  return req.requisition_status === "SUBMITTED" && req.requester_id !== userId;
}
