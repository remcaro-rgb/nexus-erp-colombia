// NIT (Colombian tax id) helpers (owner P08). The check digit (DV) uses the public DIAN modulo-11 algorithm
// with the published prime weights. This is client-side input help only; the server re-validates.
const WEIGHTS = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71];

export function normalizeNit(raw: string): string {
  return raw.replace(/[\s.\-]/g, "");
}

/** DV for a NIT body (digits only, up to 15 digits). */
export function computeDv(nitBody: string): number {
  const digits = normalizeNit(nitBody);
  if (!/^\d{1,15}$/.test(digits)) throw new Error("NIT inválido: solo dígitos (máx. 15)");
  let sum = 0;
  const rev = digits.split("").reverse();
  rev.forEach((d, i) => {
    sum += Number.parseInt(d, 10) * (WEIGHTS[i] as number);
  });
  const r = sum % 11;
  return r > 1 ? 11 - r : r;
}

export interface NitParse {
  body: string;
  dv: number | null;
  valid: boolean;
}

/** Parses "900.123.456-8" / "9001234568" (when `withDv`) and validates the DV when present. */
export function parseNit(raw: string, withDv = true): NitParse {
  const t = raw.trim();
  const m = /^([\d.\s]+)-(\d)$/.exec(t);
  if (m) {
    const body = normalizeNit(m[1] ?? "");
    const dv = Number.parseInt(m[2] ?? "", 10);
    return { body, dv, valid: /^\d{1,15}$/.test(body) && computeDv(body) === dv };
  }
  const digits = normalizeNit(t);
  if (!/^\d{2,16}$/.test(digits)) return { body: digits, dv: null, valid: false };
  if (!withDv) return { body: digits, dv: null, valid: true };
  const body = digits.slice(0, -1);
  const dv = Number.parseInt(digits.slice(-1), 10);
  return { body, dv, valid: computeDv(body) === dv };
}

export function formatNit(body: string, dv?: number | null): string {
  const grouped = normalizeNit(body).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return dv == null ? grouped : `${grouped}-${dv}`;
}
