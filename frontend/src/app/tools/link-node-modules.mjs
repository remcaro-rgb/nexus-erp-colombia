// Root postinstall (owner P08). The repository has a single pnpm lockfile at the root; frontend/ is a
// package without its own dependencies. This links frontend/node_modules -> ../node_modules so that
// `pnpm --dir frontend exec vitest|playwright|tsc` (used by the P01 Makefile) finds the binaries.
// Idempotent; never touches anything outside frontend/node_modules.
import { existsSync, lstatSync, symlinkSync, readlinkSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const link = join(root, "frontend", "node_modules");
const target = join("..", "node_modules");

if (!existsSync(join(root, "node_modules"))) {
  console.log("link-node-modules: root node_modules absent; nothing to link");
} else if (existsSync(link) || isDanglingLink(link)) {
  const st = lstatSync(link);
  if (st.isSymbolicLink() && readlinkSync(link) === target) {
    console.log("link-node-modules: frontend/node_modules already linked");
  } else {
    console.log("link-node-modules: frontend/node_modules exists and is not our link; left unchanged");
  }
} else {
  symlinkSync(target, link, "dir");
  console.log("link-node-modules: linked frontend/node_modules -> ../node_modules");
}

function isDanglingLink(p) {
  try {
    return lstatSync(p).isSymbolicLink();
  } catch {
    return false;
  }
}
