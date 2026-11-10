// Pre-step of the frontend `test` / `e2e` scripts (owner P08). The P01 Makefile runs `pnpm -r test` without a prior
// `pnpm install`; in a fresh clone node_modules is absent and tsc/vitest would resolve to nothing (or to a global
// TypeScript of another major). If the pinned toolchain is missing, install exactly from the root lockfile
// (`pnpm install --frozen-lockfile`; its postinstall links frontend/node_modules). Idempotent and offline-friendly:
// does nothing when the binaries are already installed.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const required = ["typescript", "vitest", "vite", "@playwright/test"];
const missing = required.filter(
  (p) =>
    !existsSync(join(root, "node_modules", p, "package.json")) ||
    !existsSync(join(root, "frontend", "node_modules", p, "package.json")),
);
if (missing.length === 0) process.exit(0);

console.log(`ensure-deps: missing ${missing.join(", ")}; running pnpm install --frozen-lockfile`);
const r = spawnSync("pnpm", ["install", "--frozen-lockfile"], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1" },
});
if (r.status !== 0) {
  console.error("ensure-deps: pnpm install --frozen-lockfile failed");
  process.exit(r.status ?? 1);
}
