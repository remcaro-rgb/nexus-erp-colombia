// Dates (owner P08): display "15 nov 2026" and input dd/mm/aaaa, timezone America/Bogota (D-001).
export const TIMEZONE = "America/Bogota";
export const LOCALE = "es-CO";

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];

/** ISO date "2026-11-15" -> "15 nov 2026" (calendar date, no timezone shift). */
export function formatIsoDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number.parseInt(m[2] ?? "1", 10) - 1] ?? m[2];
  return `${Number.parseInt(m[3] ?? "1", 10)} ${month} ${m[1]}`;
}

/** Instant -> "15 nov 2026, 10:00" in America/Bogota. */
export function formatInstant(isoInstant: string): string {
  const d = new Date(isoInstant);
  if (Number.isNaN(d.getTime())) return isoInstant;
  return new Intl.DateTimeFormat(LOCALE, {
    timeZone: TIMEZONE,
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/** Today's calendar date in Bogotá as ISO "YYYY-MM-DD". */
export function todayBogota(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  return parts;
}

/** "dd/mm/aaaa" -> ISO "aaaa-mm-dd" or null when invalid (real calendar check). */
export function parseDmy(input: string): string | null {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(input.trim());
  if (!m) return null;
  const d = Number.parseInt(m[1] ?? "", 10);
  const mo = Number.parseInt(m[2] ?? "", 10);
  const y = Number.parseInt(m[3] ?? "", 10);
  if (mo < 1 || mo > 12 || d < 1) return null;
  const dim = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  if (d > dim) return null;
  return `${String(y).padStart(4, "0")}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** ISO "aaaa-mm-dd" -> "dd/mm/aaaa". */
export function toDmy(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

/** Label for a period ("nov 2026"). */
export function periodLabel(startIso: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(startIso);
  if (!m) return startIso;
  return `${MONTHS[Number.parseInt(m[2] ?? "1", 10) - 1]} ${m[1]}`;
}
