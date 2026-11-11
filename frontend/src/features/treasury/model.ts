// Pure view model of treasury vouchers (no React): labels, line kinds, the received/paid total and allowed actions.
import { Dec } from "@lib/money";
import type { VoucherDto, VoucherLine, VoucherStatus } from "./api";

export const STATUS_LABEL: Record<VoucherStatus, string> = {
  DRAFT: "Borrador",
  SUBMITTED: "Por aprobar",
  APPROVED: "Aprobado",
  REJECTED: "Rechazado",
  POSTED: "Contabilizado",
  REVERSED: "Reversado",
  CANCELLED: "Anulado",
};
export const STATUS_TONE: Record<VoucherStatus, "neutral" | "brand" | "positive" | "warning" | "danger"> = {
  DRAFT: "neutral",
  SUBMITTED: "warning",
  APPROVED: "brand",
  REJECTED: "danger",
  POSTED: "positive",
  REVERSED: "danger",
  CANCELLED: "neutral",
};
export const METHOD_LABEL = { CASH: "Efectivo", TRANSFER: "Transferencia", CHECK: "Cheque", CARD: "Tarjeta", OTHER: "Otro" } as const;

export type LineKind = "APLICACION" | "CONCEPTO" | "RETENCION" | "ANTICIPO";

/** Same derivation as the backend: open item → application, concept → concept, tax code alone → withholding, else advance. */
export function lineKind(line: VoucherLine): LineKind {
  if (line.open_item_id) return "APLICACION";
  if (line.concept_id) return "CONCEPTO";
  if (line.tax_code) return "RETENCION";
  return "ANTICIPO";
}

export const KIND_LABEL: Record<LineKind, string> = {
  APLICACION: "Aplicación a factura",
  CONCEPTO: "Concepto de tesorería",
  RETENCION: "Retención",
  ANTICIPO: "Anticipo",
};

/** Money through the fund: applications + advances + concepts − withholdings (Decimal, never float). */
export function fundTotal(lines: VoucherLine[]): string {
  let total = new Dec(0);
  for (const line of lines) {
    const kind = lineKind(line);
    const value = new Dec(line.amount?.amount ?? line.applied_amount?.amount ?? line.unit_price?.amount ?? "0");
    total = kind === "RETENCION" ? total.minus(line.amount?.amount ?? "0") : total.plus(value);
  }
  return total.toFixed(4);
}

export type ActionId = "submit" | "approve" | "reject" | "post" | "reverse";

/** Actions the UI offers for a status; the server still decides (permissions, SoD, thresholds). */
export function allowedActions(v: Pick<VoucherDto, "status">, can: (code: string) => boolean, perm: string): ActionId[] {
  const out: ActionId[] = [];
  if (v.status === "DRAFT" || v.status === "REJECTED") {
    if (can(`${perm}.update`)) out.push("submit");
    if (can(`${perm}.post`)) out.push("post");
  }
  if (v.status === "SUBMITTED" && can(`${perm}.approve`)) out.push("approve", "reject");
  if (v.status === "APPROVED" && can(`${perm}.post`)) out.push("post");
  if (v.status === "POSTED" && can(`${perm}.void`)) out.push("reverse");
  return out;
}

export const ACTION_LABEL: Record<ActionId, string> = {
  submit: "Enviar a aprobación",
  approve: "Aprobar",
  reject: "Rechazar",
  post: "Contabilizar",
  reverse: "Reversar",
};

/** Actions that need a reason (contract ActionRequest: reject / reverse). */
export const NEEDS_REASON: ReadonlySet<ActionId> = new Set(["reject", "reverse"]);
