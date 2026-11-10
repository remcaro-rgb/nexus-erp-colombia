// Pure helpers for the transformation screens (no I/O, no floats; scaled BigInt with 6 decimals, D-001):
// live base-quantity balance, yield vs template tolerance (D-015), allowed actions per status.
import { fromScaled, toScaled } from "../model";
import type { TransformationAction, TransformationLineInput, TransformationStatus, TransformationType } from "./api";

export interface Balance {
  input: string;
  output: string;
  waste: string;
  difference: string;
  balanced: boolean;
}

/** Σ inputs vs Σ outputs (actual weight when given) + waste, in base units. */
export function quantityBalance(lines: TransformationLineInput[]): Balance {
  let input = 0n;
  let output = 0n;
  let waste = 0n;
  for (const ln of lines) {
    const q = toScaled(ln.quantity || "0");
    if (ln.role === "INPUT") input += q;
    else if (ln.role === "OUTPUT") output += ln.actual_weight ? toScaled(ln.actual_weight) : q;
    else waste += q;
  }
  const diff = input - output - waste;
  return { input: fromScaled(input), output: fromScaled(output), waste: fromScaled(waste), difference: fromScaled(diff), balanced: diff === 0n };
}

/** Actual yield % (6 decimals, half-up) of ``weight`` over ``input`` and whether it is inside expected ± tolerance. */
export function yieldCheck(weight: string, input: string, expected: string, tolerance: string): { actual: string; within: boolean } {
  const inp = toScaled(input);
  if (inp <= 0n) return { actual: "0", within: false };
  const SCALE = 1_000_000n;
  const num = toScaled(weight) * 100n * SCALE;
  const actualScaled = (num + inp / 2n) / inp;
  const dev = actualScaled - toScaled(expected);
  return { actual: fromScaled(actualScaled), within: (dev < 0n ? -dev : dev) <= toScaled(tolerance) };
}

export function transformationActions(status: TransformationStatus, reserved: boolean): TransformationAction[] {
  switch (status) {
    case "DRAFT":
      return [...(reserved ? [] : (["reserve"] as const)), "submit", "post", "cancel"];
    case "SUBMITTED":
      return ["approve", "reject", "cancel"];
    case "APPROVED":
      return [...(reserved ? [] : (["reserve"] as const)), "post", "cancel"];
    case "POSTED":
      return ["reverse"];
    default:
      return [];
  }
}

export const TYPE_TABS: Array<{ type: TransformationType; label: string }> = [
  { type: "PRESENTATION", label: "Presentación" },
  { type: "PORTIONING", label: "Porcionado" },
  { type: "MANUAL", label: "Manual" },
  { type: "PRODUCTION", label: "Producción" },
];
