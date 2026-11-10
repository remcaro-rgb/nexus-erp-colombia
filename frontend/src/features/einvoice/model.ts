/**
 * Facturación electrónica — pure view-model helpers (no I/O): status labels (es-CO), tones, contingency
 * countdown, provider badge and secret-reference validation. Statuses produced by the simulator are always
 * labelled "Simulado" (UI-CONTRACT; DEV-only edition).
 */

export type DocumentStatus =
  | "pending_build"
  | "built"
  | "signed"
  | "submitted"
  | "accepted"
  | "accepted_with_notices"
  | "rejected"
  | "error_retryable"
  | "contingency_issued"
  | "transmitted_late";

export type FiscalStatus = "PENDING" | "SENT" | "ACCEPTED" | "REJECTED" | "CONTINGENCY";
export type Tone = "neutral" | "brand" | "positive" | "warning" | "danger";

export interface FiscalDocument {
  id: string;
  status: DocumentStatus;
  source: { type: string; id: string; number?: string | null };
  document_type: string;
  fiscal_status: FiscalStatus;
  cufe: string | null;
  synthetic: boolean;
  version: number;
}

export interface NumberingRange {
  id: string;
  code: string;
  status: "ACTIVE" | "ARCHIVED";
  prefix: string;
  from_number: number;
  to_number: number;
  resolution: string;
  valid_to: string;
  version: number;
}

export interface TechnologyProvider {
  id: string;
  code: string;
  name: string;
  status: "ACTIVE" | "ARCHIVED";
  adapter: string;
  base_url: string;
  environment: "SANDBOX";
  contract_version?: string;
  version: number;
}

export interface ContractCase {
  case: string;
  passed: boolean;
  detail: string;
}

export interface ContractReport {
  adapter: string;
  suite_version: string;
  passed: boolean;
  cases: ContractCase[];
}

export interface ContingencyPanel {
  active: boolean;
  kind: string | null;
  since: string | null;
  restored_at: string | null;
  deadline_at: string | null;
  remaining_seconds: number | null;
  overdue: boolean;
  pending_documents: number;
}

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending_build: "Por construir",
  built: "Construido",
  signed: "Firmado",
  submitted: "Enviado",
  accepted: "Validado",
  accepted_with_notices: "Validado con notificaciones",
  rejected: "Rechazado",
  error_retryable: "Error — se reintentará",
  contingency_issued: "En contingencia",
  transmitted_late: "Transmitido tarde (contingencia)",
};

export const FAMILY_LABEL: Record<string, string> = {
  FE: "Factura electrónica",
  FE_EXPORT: "Factura de exportación",
  FE_CONTINGENCY_DIAN: "Factura por contingencia DIAN",
  FE_CONTINGENCY_ISSUER: "Factura de contingencia (facturador)",
  NC: "Nota crédito",
  ND: "Nota débito",
  DEE_POS: "Documento equivalente POS",
  DS: "Documento soporte",
  DS_ADJUSTMENT: "Nota de ajuste al documento soporte",
  NOMINA: "Nómina electrónica",
  NOMINA_AJUSTE: "Nómina de ajuste",
  FE_RECEIVED: "Factura recibida",
};

export const SIMULATED_LABEL = "Simulado";
export const PROVIDER_BADGE = "SIMULADO — no certificado";

export function statusTone(status: DocumentStatus): Tone {
  switch (status) {
    case "accepted":
      return "positive";
    case "accepted_with_notices":
    case "error_retryable":
    case "contingency_issued":
    case "transmitted_late":
      return "warning";
    case "rejected":
      return "danger";
    default:
      return "neutral";
  }
}

/** Text shown next to a status: the simulator's statuses always carry "Simulado" (never colour alone). */
export function statusText(doc: Pick<FiscalDocument, "status" | "synthetic">): string {
  const label = STATUS_LABEL[doc.status] ?? doc.status;
  return doc.synthetic ? `${label} · ${SIMULATED_LABEL}` : label;
}

export const RETRYABLE: ReadonlySet<DocumentStatus> = new Set(["error_retryable", "built", "signed", "contingency_issued"]);
export const DELIVERABLE: ReadonlySet<DocumentStatus> = new Set(["accepted", "accepted_with_notices"]);

/** "1 d 03 h 05 min" style countdown (es-CO); null when there is no deadline. */
export function formatCountdown(seconds: number | null): string | null {
  if (seconds === null || seconds === undefined) return null;
  if (seconds <= 0) return "Plazo vencido";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return d > 0 ? `${d} d ${pad(h)} h ${pad(m)} min` : `${pad(h)} h ${pad(m)} min`;
}

export function contingencyMessage(panel: ContingencyPanel | null | undefined): { tone: Tone; text: string } | null {
  if (!panel) return null;
  if (panel.active) {
    const why = panel.kind === "PROVIDER_OUTAGE" ? "DIAN o el proveedor tecnológico no responde" : "declarada por la empresa";
    return { tone: "warning", text: `Contingencia activa (${why}). Los documentos se emiten en contingencia y se transmitirán al restablecerse el servicio.` };
  }
  if (panel.deadline_at && panel.pending_documents > 0) {
    const left = formatCountdown(panel.remaining_seconds);
    return panel.overdue
      ? { tone: "danger", text: `Plazo de 48 h vencido: ${panel.pending_documents} documento(s) de contingencia sin transmitir.` }
      : { tone: "warning", text: `Transmita ${panel.pending_documents} documento(s) de contingencia. Tiempo restante: ${left}.` };
  }
  return null;
}

/** Remaining numbers of a range (`last used` is not in the contract: the KPI endpoint gives the minimum). */
export function rangeSize(r: Pick<NumberingRange, "from_number" | "to_number">): number {
  return r.to_number - r.from_number + 1;
}

/** `secret` + `://einvoice/` — references to the operator's secret files (never values). */
export const SECRET_REF_PREFIX = ["secret", "//einvoice/"].join(":");

const SECRET_REF = /^secret:\/\/einvoice\/[a-z0-9][a-z0-9._-]{0,99}$/;

/** Credentials are referenced, never typed in: only `secret://einvoice/<nombre>` is accepted. */
export function isSecretRef(value: string): boolean {
  return SECRET_REF.test(value.trim());
}

export function failedCases(report: ContractReport | null | undefined): string[] {
  return (report?.cases ?? []).filter((c) => !c.passed).map((c) => c.case);
}

/** Drill filter from the dashboard (`?estado=validado|rejected|…`). */
export function statusFilterFromQuery(search: string): string | null {
  const v = new URLSearchParams(search).get("estado");
  return v && (v === "validado" || v in STATUS_LABEL) ? v : null;
}
