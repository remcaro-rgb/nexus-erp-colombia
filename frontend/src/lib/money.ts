// Decimal-safe money and quantity helpers (owner P08). Values travel as strings ("1234567.8900");
// arithmetic uses decimal.js; `number` is never used for amounts (constitution: no float).
import Decimal from "decimal.js";

/** Decimal constructor with precision 28 and ROUND_HALF_UP (D-001). */
export const Dec = Decimal.clone({ precision: 40, rounding: Decimal.ROUND_HALF_UP, toExpNeg: -40, toExpPos: 40 });
export type Dec = InstanceType<typeof Dec>;

/** Contract precisions (C2): amount 20,4 · quantity 20,6 · unit cost 24,8 · FX 24,10 · percent 9,6. */
export const PRECISION = {
  amount: { digits: 20, scale: 4 },
  quantity: { digits: 20, scale: 6 },
  unitCost: { digits: 24, scale: 8 },
  fxRate: { digits: 24, scale: 10 },
  percent: { digits: 9, scale: 6 },
} as const;
export type PrecisionKind = keyof typeof PRECISION;

const CANONICAL = /^-?\d+(\.\d+)?$/;

export class DecimalInputError extends Error {
  constructor(
    public readonly code: "empty" | "invalid" | "too_many_decimals" | "too_many_digits",
    message: string,
  ) {
    super(message);
  }
}

/**
 * Parse user input written in es-CO ("1.234.567,89", "$ 1.234,5", "-12,3") into a canonical
 * decimal string ("1234567.89"). Thousands separator "." and decimal separator "," (D-001).
 * Throws DecimalInputError; never returns NaN and never goes through Number().
 */
export function parseEsCoDecimal(input: string, kind: PrecisionKind = "amount"): string {
  let s = input.replace(/[\s ]/g, "").replace(/^\$/, "").replace(/COP$/i, "");
  if (s === "" || s === "-") throw new DecimalInputError("empty", "Ingrese un valor.");
  let neg = false;
  if (s.startsWith("-")) {
    neg = true;
    s = s.slice(1);
  }
  s = s.replace(/^\$/, "");
  if (!/^[\d.,]+$/.test(s)) throw new DecimalInputError("invalid", "Use solo dígitos, puntos de miles y coma decimal.");
  const parts = s.split(",");
  if (parts.length > 2) throw new DecimalInputError("invalid", "Use una sola coma decimal.");
  const intRaw = parts[0] ?? "";
  const frac = parts[1] ?? "";
  if (frac.includes(".")) throw new DecimalInputError("invalid", "Los puntos solo separan miles.");
  if (intRaw.includes(".") && !/^\d{1,3}(\.\d{3})+$/.test(intRaw)) {
    throw new DecimalInputError("invalid", "Los puntos de miles deben separar grupos de tres dígitos.");
  }
  const intDigits = intRaw.replace(/\./g, "") || "0";
  const { digits, scale } = PRECISION[kind];
  if (frac.length > scale) throw new DecimalInputError("too_many_decimals", `Máximo ${scale} decimales.`);
  const intClean = intDigits.replace(/^0+(?=\d)/, "");
  if (intClean.length > digits - scale) throw new DecimalInputError("too_many_digits", `Máximo ${digits - scale} dígitos enteros.`);
  const canonical = frac.length ? `${intClean}.${frac}` : intClean;
  const isZero = /^0(\.0*)?$/.test(canonical);
  return neg && !isZero ? `-${canonical}` : canonical;
}

/** Canonical string -> es-CO grouped string with exactly `minFraction..maxFraction` decimals. */
export function formatEsCoDecimal(value: string, opts: { minFraction?: number; maxFraction?: number } = {}): string {
  if (!CANONICAL.test(value)) throw new DecimalInputError("invalid", `valor no canónico: ${value}`);
  const d = new Dec(value);
  const max = opts.maxFraction ?? 4;
  const min = opts.minFraction ?? 0;
  let fixed = d.toFixed(max, Decimal.ROUND_HALF_UP);
  let [i, f = ""] = fixed.replace("-", "").split(".");
  f = f.replace(/0+$/, "");
  while (f.length < min) f += "0";
  const grouped = (i ?? "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const neg = fixed.startsWith("-") && !/^-0(\.0+)?$/.test(fixed);
  fixed = `${neg ? "-" : ""}${grouped}${f ? "," + f : ""}`;
  return fixed;
}

/** COP display per kpi_semantics.units.COP: "$ 1.284.500" (no decimals by default). */
export function formatCOP(value: string, opts: { decimals?: number } = {}): string {
  const dec = opts.decimals ?? 0;
  const body = formatEsCoDecimal(value, { minFraction: dec, maxFraction: dec });
  return body.startsWith("-") ? `-$ ${body.slice(1)}` : `$ ${body}`;
}

/** Compact COP ("$ 1,3 M"); callers must expose the full value through aria-label/tooltip. */
export function formatCOPCompact(value: string): string {
  const d = new Dec(value);
  const abs = d.abs();
  const sign = d.isNegative() ? "-" : "";
  const units: Array<[string, string]> = [
    ["1000000000000", "B"],
    ["1000000000", "MM"],
    ["1000000", "M"],
    ["1000", "mil"],
  ];
  for (const [base, suffix] of units) {
    if (abs.gte(base)) {
      const q = abs.div(base).toDecimalPlaces(1, Decimal.ROUND_HALF_UP).toFixed();
      return `${sign}$ ${formatEsCoDecimal(q, { maxFraction: 1 })} ${suffix}`;
    }
  }
  return `${sign}${formatCOP(abs.toFixed())}`;
}

/** Decimal-safe sum of canonical strings. */
export function sumDecimals(values: ReadonlyArray<string | null | undefined>): string {
  let acc = new Dec(0);
  for (const v of values) if (v != null && v !== "") acc = acc.plus(v);
  return acc.toFixed();
}

/** Round to the currency minor unit (COP 2) ROUND_HALF_UP (D-001). */
export function roundCurrency(value: string, scale = 2): string {
  return new Dec(value).toDecimalPlaces(scale, Decimal.ROUND_HALF_UP).toFixed(scale);
}

export function isCanonicalDecimal(value: string): boolean {
  return CANONICAL.test(value);
}

/** Decimal equality on canonical strings ("1.50" == "1.5"). */
export function decimalEquals(a: string, b: string): boolean {
  try {
    return new Dec(a).eq(b);
  } catch {
    return false;
  }
}
