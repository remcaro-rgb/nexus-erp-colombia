// Pure view-model helpers of the purchasing screens (no I/O; unit-tested). Decimal strings only (no float).
import { formatCOP } from "@lib/money";
import type { ComparedQuote, ComparisonDto, Criterion, QuoteStatus, RfqStatus } from "./api";

export const CRITERIA: Array<{ id: Criterion; label: string }> = [
  { id: "price", label: "Precio puesto" },
  { id: "lead_time", label: "Plazo de entrega" },
  { id: "rating", label: "Calificación" },
  { id: "payment_terms", label: "Condiciones de pago" },
];

export const RFQ_STATUS_LABEL: Record<RfqStatus, string> = {
  DRAFT: "Borrador",
  SENT: "Enviada",
  CLOSED: "Cerrada",
  EVALUATED: "Evaluada",
  AWARD_PENDING_APPROVAL: "Adjudicación por aprobar",
  AWARDED: "Adjudicada",
  PO_CREATED: "Orden creada",
  CANCELLED: "Anulada",
};

export const QUOTE_STATUS_LABEL: Record<QuoteStatus, string> = {
  RECEIVED: "Recibida",
  LATE: "Tardía (no puntúa)",
  WITHDRAWN: "Retirada",
  DISQUALIFIED: "Descalificada",
  AWARDED: "Adjudicada",
};

export const EXCLUSION_LABEL: Record<string, string> = {
  LATE: "Tardía: no puntúa hasta reabrir (D-010)",
  WITHDRAWN: "Retirada",
  DISQUALIFIED: "Descalificada",
  INCOMPLETE: "No cubre todas las líneas",
};

export interface MatrixRow {
  quoteId: string;
  vendor: string;
  rank: number | null;
  total: string;
  score: string | null;
  points: Record<Criterion, string> | null;
  recommended: boolean;
  excluded: string | null;
}

/** Rows of the comparison matrix: ranked quotes first (server order, deterministic), then the excluded ones. */
export function matrixRows(c: ComparisonDto): MatrixRow[] {
  const row = (q: ComparedQuote, excluded: string | null): MatrixRow => ({
    quoteId: q.quote_id,
    vendor: `${q.vendor_code} — ${q.vendor_name}`,
    rank: q.rank ?? null,
    total: formatCOP(q.comparable_total_cop),
    score: q.score ?? null,
    points: q.points ?? null,
    recommended: q.quote_id === c.recommended_quote_id,
    excluded,
  });
  return [...c.ranked.map((q) => row(q, null)), ...c.excluded.map((q) => row(q, EXCLUSION_LABEL[q.reason ?? ""] ?? q.reason ?? ""))];
}

export function recommendationBadge(c: ComparisonDto): string {
  const top = c.ranked.find((q) => q.quote_id === c.recommended_quote_id);
  if (!top) return "Sin recomendación";
  return `Recomendada: ${top.vendor_code} (${top.score} puntos)`;
}

export interface AwardDraft {
  quoteId: string | null;
  reason: string;
}

/** Client-side mirror of the server rule (award_reason_required); the server stays the authority. */
export function awardErrors(c: ComparisonDto, draft: AwardDraft): string[] {
  const errors: string[] = [];
  if (!draft.quoteId) errors.push("Seleccione la cotización a adjudicar.");
  else if (!c.ranked.some((q) => q.quote_id === draft.quoteId)) errors.push("La cotización está excluida del comparativo.");
  else if (draft.quoteId !== c.recommended_quote_id && !draft.reason.trim())
    errors.push("La adjudicación difiere de la recomendación: indique el motivo.");
  return errors;
}

export function differsFromRecommendation(c: ComparisonDto, quoteId: string | null): boolean {
  return Boolean(quoteId) && quoteId !== c.recommended_quote_id;
}
