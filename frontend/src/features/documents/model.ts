// Pure helpers of the template editor: a client-side pre-check of the sandboxed syntax (the server is authoritative)
// and a safe preview frame (the server's escaped HTML shown in a sandboxed iframe without scripts).

const NAME = /^[A-Za-z][A-Za-z0-9_]*$/;
const TAG = /(\{\{.*?\}\}|\{%.*?%\})/gs;

/** First problem found in a template body, or null: forbidden names (``__``, leading ``_``), unbalanced blocks. */
export function precheck(body: string): string | null {
  if (body.length > 10_000) return "La plantilla supera 10.000 caracteres";
  const stack: string[] = [];
  for (const m of body.matchAll(TAG)) {
    const token = m[0];
    const inner = token.slice(2, -2).trim();
    if (token.startsWith("{{")) {
      const path = (inner.split("|")[0] ?? "").trim();
      for (const part of path.split(".")) {
        if (!(NAME.test(part) || /^\d{1,4}$/.test(part)) || part.includes("__") || part.startsWith("_")) {
          return `Expresión no permitida: ${token}`;
        }
      }
      continue;
    }
    const [tag] = inner.split(/\s+/);
    if (tag === "for" || tag === "if") stack.push(tag);
    else if (tag === "endfor" || tag === "endif") {
      if (stack.pop() !== tag.slice(3)) return `Etiqueta ${token} sin abrir`;
    } else if (tag !== "else") return `Etiqueta desconocida: ${token}`;
  }
  return stack.length ? `Falta cerrar {% ${stack[stack.length - 1]} %}` : null;
}

export function previewDocument(html: string): string {
  return `<!doctype html><html lang="es-CO"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><style>body{font-family:sans-serif;font-size:12px}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:2px 4px;text-align:left}.doc-watermark{font-weight:bold;text-align:center}</style></head><body>${html}</body></html>`;
}

/** Weak ETag of a versioned resource as the server computes it (W/"sha256(label:id:version)[:32]"): lets the editor
 * send If-Match for the version it loaded, so a concurrent change answers 412 instead of being overwritten. */
export async function etagFor(label: string, id: string, version: number): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${label}:${id}:${version}`));
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  return `W/"${hex.slice(0, 32)}"`;
}
