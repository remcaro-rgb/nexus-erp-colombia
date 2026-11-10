// Client-side tokenizer for SYNTAX HIGHLIGHTING ONLY (owner P08). It never evaluates anything.
export type TokenKind = "number" | "string" | "ident" | "func" | "op" | "paren" | "comma" | "space" | "unknown";

export interface Token {
  kind: TokenKind;
  text: string;
  start: number;
  end: number;
}

export const FUNCTIONS = new Set([
  "if", "min", "max", "abs", "round", "floor", "ceil", "clamp", "lookup", "days_between", "month", "year", "percent", "and", "or", "not",
]);

export function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i] as string;
    const start = i;
    if (/\s/.test(c)) {
      while (i < src.length && /\s/.test(src[i] as string)) i++;
      out.push({ kind: "space", text: src.slice(start, i), start, end: i });
    } else if (/\d/.test(c)) {
      while (i < src.length && /[\d.]/.test(src[i] as string)) i++;
      out.push({ kind: "number", text: src.slice(start, i), start, end: i });
    } else if (c === '"' || c === "'") {
      i++;
      while (i < src.length && src[i] !== c) i++;
      i = Math.min(src.length, i + 1);
      out.push({ kind: "string", text: src.slice(start, i), start, end: i });
    } else if (/[A-Za-z_áéíóúñÁÉÍÓÚÑ]/.test(c)) {
      while (i < src.length && /[\wáéíóúñÁÉÍÓÚÑ]/.test(src[i] as string)) i++;
      const text = src.slice(start, i);
      out.push({ kind: FUNCTIONS.has(text.toLowerCase()) ? "func" : "ident", text, start, end: i });
    } else if ("()".includes(c)) {
      i++;
      out.push({ kind: "paren", text: c, start, end: i });
    } else if (c === ",") {
      i++;
      out.push({ kind: "comma", text: c, start, end: i });
    } else if ("+-*/%<>=!".includes(c)) {
      i++;
      if ("<>=!".includes(c) && src[i] === "=") i++;
      out.push({ kind: "op", text: src.slice(start, i), start, end: i });
    } else {
      i++;
      out.push({ kind: "unknown", text: c, start, end: i });
    }
  }
  return out;
}

/** 0-based offset -> 1-based line/column. */
export function lineCol(src: string, offset: number): { line: number; col: number } {
  let line = 1;
  let col = 1;
  for (let i = 0; i < Math.min(offset, src.length); i++) {
    if (src[i] === "\n") {
      line++;
      col = 1;
    } else col++;
  }
  return { line, col };
}
