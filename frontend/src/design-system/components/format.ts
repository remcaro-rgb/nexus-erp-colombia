// KPI unit formatting (owner P08) per module-registry.json › kpi_semantics.units. Inputs are decimal strings.
import { Dec, formatCOP, formatCOPCompact, formatEsCoDecimal } from "@lib/money";

export function formatUnit(unit: string, value: string): string {
  switch (unit) {
    case "COP":
      return formatCOP(value);
    case "count":
      return formatEsCoDecimal(new Dec(value).toDecimalPlaces(0).toFixed(), { maxFraction: 0 });
    case "percent":
      return `${formatEsCoDecimal(new Dec(value).toDecimalPlaces(1).toFixed(1), { minFraction: 1, maxFraction: 1 })} %`;
    case "ratio":
      return `${formatEsCoDecimal(new Dec(value).toDecimalPlaces(1).toFixed(1), { minFraction: 1, maxFraction: 1 })}x`;
    case "days":
      return `${formatEsCoDecimal(new Dec(value).toDecimalPlaces(0).toFixed(), { maxFraction: 0 })} días`;
    case "hours":
      return `${formatEsCoDecimal(new Dec(value).toDecimalPlaces(1).toFixed(1), { minFraction: 1, maxFraction: 1 })} h`;
    default:
      return formatEsCoDecimal(value, { maxFraction: 4 });
  }
}

/** Short form for tight spaces (COP compact); the full value must stay available to screen readers. */
export function formatUnitShort(unit: string, value: string): string {
  return unit === "COP" ? formatCOPCompact(value) : formatUnit(unit, value);
}

/** Delta % string ("4.25") -> "+4,3 %"; null -> null (zero_denominator rule shows "—"). */
export function formatDelta(delta: string | null): { text: string; tone: "positive" | "negative" | "neutral" } | null {
  if (delta === null || delta === "") return null;
  const d = new Dec(delta);
  const text = `${d.isPositive() && !d.isZero() ? "+" : ""}${formatEsCoDecimal(d.toDecimalPlaces(1).toFixed(1), { minFraction: 1, maxFraction: 1 })} %`;
  return { text, tone: d.isZero() ? "neutral" : d.isPositive() ? "positive" : "negative" };
}
