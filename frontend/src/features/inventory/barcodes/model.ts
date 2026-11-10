// Pure barcode helpers for the UI (no I/O): keyboard-wedge scan normalization, GS1 mod-10 check digit (instant
// feedback before the server's :validate) and the bar geometry of a label preview (SVG rects from the module string).
// The server stays authoritative (inventory/barcodes/core.py implements the same rules; T-INV-BARCODE-01 vectors).

/** Keyboard-wedge scanners type the code and send Enter/Tab: strip CR/LF/TAB and spaces around the value. */
export function normalizeScan(raw: string): string {
  return raw.replace(/[\r\n\t]+/g, "").trim();
}

/** GS1 mod-10 check digit of a GTIN body (weights 3,1,… from the right). */
export function gtinCheckDigit(body: string): number {
  if (!/^\d+$/.test(body)) throw new Error("GTIN bodies are digits only");
  let total = 0;
  [...body].reverse().forEach((d, i) => {
    total += Number(d) * (i % 2 === 0 ? 3 : 1);
  });
  return (10 - (total % 10)) % 10;
}

export function isValidGtin(value: string): boolean {
  const v = normalizeScan(value);
  if (!/^\d+$/.test(v) || ![8, 12, 13, 14].includes(v.length)) return false;
  return gtinCheckDigit(v.slice(0, -1)) === Number(v.slice(-1));
}

/** GS1 restricted-circulation prefixes 20–29: internal use, never a GS1 licence (D-004). */
export function isRestrictedCirculation(value: string): boolean {
  const v = normalizeScan(value);
  return v.length === 13 && /^2\d/.test(v);
}

export interface Bar {
  x: number;
  width: number;
}

/** Runs of "1" in the module string → bars (x and width in modules), quiet zone offset included. */
export function bars(modules: string, quiet = 10): Bar[] {
  const out: Bar[] = [];
  let i = 0;
  while (i < modules.length) {
    if (modules[i] === "1") {
      let j = i;
      while (j < modules.length && modules[j] === "1") j += 1;
      out.push({ x: quiet + i, width: j - i });
      i = j;
    } else {
      i += 1;
    }
  }
  return out;
}

/** Human text shown under the bars, in the template's fixed field order (labels never carry cost). */
export const LABEL_FIELDS = ["value", "code", "name", "presentation", "lot", "expiry_date", "location", "company"] as const;

export function labelLines(text: Record<string, string>, fields: readonly string[]): string[] {
  const out = fields.filter((f) => (LABEL_FIELDS as readonly string[]).includes(f) && text[f]).map((f) => text[f] as string);
  if (text.notice) out.push(text.notice);
  return out;
}
