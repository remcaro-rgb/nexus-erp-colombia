/**
 * Employee portal model (F-PORTAL-EMP, owner P13). Pure functions over the portal API payloads
 * (`/api/portal/v1/employee/*`). Amounts and days are decimal strings (never JS numbers in transit).
 */

export interface Money {
  amount: string;
  currency: string;
}

export interface EmployeePayslip {
  id: string;
  status: string;
  period: string;
  net_pay: Money | null;
  version: number;
}

export type CertificateType = "LABORAL" | "INGRESOS_RETENCIONES";

export interface EmployeeCertificate {
  id: string;
  status: string;
  certificate_type: CertificateType;
  year: number;
}

export type VacationStatus = "SUBMITTED" | "APPROVED" | "REJECTED" | "TAKEN";

export interface EmployeeVacation {
  id: string;
  start_date: string;
  end_date: string;
  days: string;
  request_status: VacationStatus;
}

export interface EmployeeDataUpdate {
  id: string;
  changes: Record<string, string>;
  review_status: "SUBMITTED" | "APPROVED" | "REJECTED";
}

export interface EmployeeProfile {
  id: string;
  full_name: string;
  email: string | null;
  position: string | null;
}

export interface PortalKpi {
  id: string;
  value: string | null;
  previous: string | null;
  unit: string;
  state: "ok" | "no_data" | "not_available" | "forbidden_field";
}

/** Fields an employee may ask to change (HR approves); everything else is refused by the API too. */
export const UPDATABLE_FIELDS = [
  { id: "email", label: "Correo electrónico" },
  { id: "phone", label: "Teléfono" },
  { id: "address", label: "Dirección" },
] as const;

export const CERTIFICATE_LABEL: Record<CertificateType, string> = {
  LABORAL: "Certificado laboral",
  INGRESOS_RETENCIONES: "Certificado de ingresos y retenciones",
};

export const FIXTURE_NOTICE = "SINTÉTICO — NO LEGALMENTE VERIFICADO (formato de desarrollo, no certificado)";

export const VACATION_LABEL: Record<VacationStatus, string> = {
  SUBMITTED: "Pendiente de aprobación",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
  TAKEN: "Disfrutada",
};

export function vacationTone(s: VacationStatus): "warning" | "positive" | "danger" | "neutral" {
  return s === "SUBMITTED" ? "warning" : s === "APPROVED" ? "positive" : s === "REJECTED" ? "danger" : "neutral";
}

/** es-CO COP formatting of a decimal string without floating point (thousands ".", decimals ","). */
export function formatCop(m: Money | null): string {
  if (!m) return "•••";
  const [int = "0", frac = ""] = m.amount.replace(/^-/, "").split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const cents = (frac + "00").slice(0, 2);
  const sign = m.amount.startsWith("-") ? "-" : "";
  return `${sign}$ ${grouped}${cents === "00" ? "" : "," + cents}`;
}

/** Business days (Mon–Fri) between two ISO dates inclusive — an estimate only; holidays are applied by the server. */
export function estimateBusinessDays(start: string, end: string): number {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return 0;
  const d = new Date(`${start}T12:00:00Z`);
  const last = new Date(`${end}T12:00:00Z`);
  let n = 0;
  while (d <= last) {
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) n += 1;
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return n;
}

/** Client-side validation of a vacation request (the server re-checks balance and holidays). */
export function validateVacation(start: string, end: string, available: string | null): string | null {
  if (!start || !end) return "Indique la fecha inicial y la final.";
  if (end < start) return "La fecha final es anterior a la inicial.";
  const days = estimateBusinessDays(start, end);
  if (days === 0) return "El periodo no tiene días hábiles.";
  if (available !== null && Number.isFinite(Number(available)) && days > Number(available)) {
    return "Saldo de vacaciones insuficiente.";
  }
  return null;
}

/** Only allowed, changed, non-empty values are sent. */
export function dataUpdateChanges(form: Record<string, string>, current: Partial<Record<string, string | null>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of UPDATABLE_FIELDS) {
    const v = (form[f.id] ?? "").trim();
    if (v && v !== (current[f.id] ?? "")) out[f.id] = v;
  }
  return out;
}

export function kpiById(kpis: PortalKpi[], id: string): PortalKpi | undefined {
  return kpis.find((k) => k.id === id);
}
