// Date formatting for purchasing screens (es-CO, America/Bogota; D-001).
export function formatDateTimeBogota(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", dateStyle: "medium", timeStyle: "short" }).format(d);
}
