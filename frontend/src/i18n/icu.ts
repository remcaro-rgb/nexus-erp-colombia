// Minimal ICU MessageFormat subset (owner P08): {arg}, {n, plural, =0 {..} one {..} other {..}} with '#',
// {x, select, a {..} other {..}}. Pure string handling: output is text, never HTML.
export type MessageValues = Record<string, string | number | null | undefined>;

interface Ctx {
  locale: string;
  values: MessageValues;
  pound?: string;
}

function readBlock(src: string, start: number): [string, number] {
  // src[start] === "{"; returns [inner, indexAfterClosing]
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    const c = src[i];
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return [src.slice(start + 1, i), i + 1];
    }
  }
  throw new Error(`ICU: unbalanced braces in "${src}"`);
}

function splitOptions(body: string): Map<string, string> {
  const opts = new Map<string, string>();
  let i = 0;
  while (i < body.length) {
    while (i < body.length && /\s/.test(body[i] as string)) i++;
    if (i >= body.length) break;
    let key = "";
    while (i < body.length && body[i] !== "{" && !/\s/.test(body[i] as string)) key += body[i++];
    while (i < body.length && /\s/.test(body[i] as string)) i++;
    if (body[i] !== "{") throw new Error(`ICU: option "${key}" needs a {message}`);
    const [inner, next] = readBlock(body, i);
    opts.set(key, inner);
    i = next;
  }
  return opts;
}

function render(src: string, ctx: Ctx): string {
  let out = "";
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === "{") {
      const [inner, next] = readBlock(src, i);
      out += renderArg(inner, ctx);
      i = next;
    } else if (c === "#" && ctx.pound !== undefined) {
      out += ctx.pound;
      i++;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

function renderArg(inner: string, ctx: Ctx): string {
  const first = inner.indexOf(",");
  if (first < 0) {
    const v = ctx.values[inner.trim()];
    return v === null || v === undefined ? "" : typeof v === "number" ? new Intl.NumberFormat(ctx.locale).format(v) : v;
  }
  const name = inner.slice(0, first).trim();
  const rest = inner.slice(first + 1);
  const second = rest.indexOf(",");
  const type = (second < 0 ? rest : rest.slice(0, second)).trim();
  const body = second < 0 ? "" : rest.slice(second + 1);
  const v = ctx.values[name];
  const opts = splitOptions(body);
  if (type === "plural") {
    const n = typeof v === "number" ? v : Number.parseInt(String(v ?? "0"), 10);
    const exact = opts.get(`=${n}`);
    const cat = new Intl.PluralRules(ctx.locale).select(n);
    const msg = exact ?? opts.get(cat) ?? opts.get("other") ?? "";
    return render(msg, { ...ctx, pound: new Intl.NumberFormat(ctx.locale).format(n) });
  }
  if (type === "select") {
    const msg = opts.get(String(v ?? "")) ?? opts.get("other") ?? "";
    return render(msg, ctx);
  }
  throw new Error(`ICU: unsupported argument type "${type}"`);
}

export function formatIcu(message: string, values: MessageValues = {}, locale = "es-CO"): string {
  return render(message, { locale, values });
}
