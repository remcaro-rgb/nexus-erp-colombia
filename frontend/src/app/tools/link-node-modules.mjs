// Root postinstall (owner P08). The repository has a single pnpm lockfile at the root; frontend/ is a package
// without its own dependencies. This creates frontend/node_modules/ as a REAL directory (so the repo's
// `node_modules/` ignore rule covers it) whose entries are symlinks to the root node_modules entries. Then
// `pnpm --dir frontend exec vitest|playwright|tsc` (P01 Makefile) finds the binaries and their relative shims.
// Idempotent; touches nothing outside frontend/node_modules.
import { existsSync, lstatSync, mkdirSync, readdirSync, readlinkSync, symlinkSync, unlinkSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const src = join(root, "node_modules");
const dst = join(root, "frontend", "node_modules");

function lstat(p) {
  try {
    return lstatSync(p);
  } catch {
    return null;
  }
}

if (!existsSync(src)) {
  console.log("link-node-modules: root node_modules absent; nothing to link");
  process.exit(0);
}
const st = lstat(dst);
if (st?.isSymbolicLink()) unlinkSync(dst); // older layout (single symlink) -> real directory
if (!lstat(dst)) mkdirSync(dst);
if (!lstat(dst).isDirectory()) {
  console.log("link-node-modules: frontend/node_modules is not a directory; left unchanged");
  process.exit(0);
}
let made = 0;
for (const name of readdirSync(src)) {
  const target = join("..", "..", "node_modules", name);
  const at = join(dst, name);
  const cur = lstat(at);
  if (cur?.isSymbolicLink() && readlinkSync(at) === target) continue;
  if (cur) continue; // something real is there: never overwrite
  symlinkSync(target, at);
  made++;
}
console.log(`link-node-modules: frontend/node_modules ready (${made} new links)`);
