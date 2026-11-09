// Performance budget (owner P08, prompt deliverable 11): initial JS (entry + modulepreloads referenced by
// dist/index.html) must stay < 250 KB gzip. Run after `vite build` (pnpm --dir frontend run budget).
// LCP/INP budgets are measured by the Lighthouse/visual suites (P22) against a running stack.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dist = resolve(dirname(fileURLToPath(import.meta.url)), "../../../dist");
const html = readFileSync(join(dist, "index.html"), "utf-8");
const refs = [...html.matchAll(/<(?:script[^>]*src|link[^>]*rel="modulepreload"[^>]*href)="([^"]+\.js)"/g)].map((m) => m[1]);
let total = 0;
for (const r of refs) {
  const size = gzipSync(readFileSync(join(dist, r.replace(/^\//, "")))).length;
  total += size;
  console.log(`${(size / 1024).toFixed(1).padStart(8)} KB gz  ${r}`);
}
const limit = 250 * 1024;
console.log(`initial JS: ${(total / 1024).toFixed(1)} KB gz (budget 250 KB)`);
if (refs.length === 0 || total > limit) {
  console.error("BUDGET-FAILED");
  process.exit(1);
}
console.log("BUDGET-OK");
