// Pure view-model helpers for stock issues (no I/O): allowed actions per state, client-side pre-validation that mirrors
// the server rules (dimension_required, issue_reason_required) and es-CO money formatting from decimal strings.
import type { DimensionKey, DimensionSlots, IssueAction, IssueConceptDto, IssueInput, IssueStatus } from "./api";

export const SLOT: Record<DimensionKey, keyof DimensionSlots> = {
  cost_center: "cost_center_id",
  business_unit: "business_unit_id",
  aux_abierto: "aux_abierto_id",
  item_contable: "item_dimension_id",
};

export const DIMENSION_LABEL: Record<DimensionKey, string> = {
  cost_center: "Centro de costo",
  business_unit: "Unidad de negocio",
  aux_abierto: "Auxiliar abierto",
  item_contable: "Ítem contable (proyecto)",
};

export const STATUS_LABEL: Record<IssueStatus, string> = {
  DRAFT: "Borrador",
  SUBMITTED: "Pendiente de aprobación",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  POSTED: "Contabilizada",
  REVERSED: "Reversada",
  CANCELLED: "Anulada",
};

/** Actions the document pattern offers in each state (the server re-checks every transition). */
export function issueActions(status: IssueStatus): IssueAction[] {
  switch (status) {
    case "DRAFT":
      return ["simulate", "submit", "post"];
    case "SUBMITTED":
      return ["approve"];
    case "APPROVED":
      return ["simulate", "post"];
    case "POSTED":
      return ["reverse"];
    default:
      return [];
  }
}

export function requiredDimensions(concept: IssueConceptDto): DimensionKey[] {
  const keys: DimensionKey[] = concept.requires_dimension ? ["cost_center"] : [];
  for (const d of concept.required_dims) if (!keys.includes(d)) keys.push(d);
  return keys;
}

export interface DraftProblem {
  code: "issue_concept_inactive" | "issue_reason_required" | "dimension_required" | "lines_required" | "quantity_invalid";
  message: string;
  pointer: string;
}

const DECIMAL = /^\d{1,14}(\.\d{1,6})?$/;

/** Same rules as the server, so the form can flag problems before the request (the server stays authoritative). */
export function validateDraft(concept: IssueConceptDto, input: IssueInput): DraftProblem[] {
  const out: DraftProblem[] = [];
  if (concept.status !== "ACTIVE") out.push({ code: "issue_concept_inactive", message: `El concepto ${concept.code} está inactivo`, pointer: "/concept_id" });
  const reason = (input.reason ?? "").trim();
  if (concept.requires_reason) {
    const head = reason.replace(":", " ").split(/\s+/)[0]?.toUpperCase() ?? "";
    if (!reason) out.push({ code: "issue_reason_required", message: "El concepto exige un motivo", pointer: "/reason" });
    else if (concept.reason_codes.length > 0 && !concept.reason_codes.includes(head))
      out.push({ code: "issue_reason_required", message: `El motivo empieza por ${concept.reason_codes.join(", ")}`, pointer: "/reason" });
  }
  if (input.lines.length === 0) out.push({ code: "lines_required", message: "Agregue al menos una línea", pointer: "/lines" });
  input.lines.forEach((ln, i) => {
    const q = ln.quantity ?? ln.qty_packages ?? "";
    if (!DECIMAL.test(q) || /^0+(\.0+)?$/.test(q)) out.push({ code: "quantity_invalid", message: `Línea ${i + 1}: cantidad inválida`, pointer: `/lines/${i}/quantity` });
    for (const key of requiredDimensions(concept)) {
      const slot = SLOT[key];
      if (!(ln.dimensions?.[slot] ?? input.dimensions?.[slot])) {
        out.push({ code: "dimension_required", message: `Línea ${i + 1}: falta ${DIMENSION_LABEL[key]}`, pointer: `/lines/${i}/dimensions/${slot}` });
      }
    }
  });
  return out;
}

/** es-CO money display from a decimal string ("1234567.5050" → "$ 1.234.567,51", ROUND_HALF_UP); never through float. */
export function formatCop(amount: string | undefined | null): string {
  if (!amount) return "—";
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount.trim());
  if (!m) return amount;
  const digits = m[3] ?? "";
  let cents = BigInt(m[2] ?? "0") * 100n + BigInt((digits + "00").slice(0, 2));
  if ((digits[2] ?? "0") >= "5") cents += 1n;
  const int = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const frac = (cents % 100n).toString().padStart(2, "0");
  return `${m[1]}$ ${int},${frac}`;
}
