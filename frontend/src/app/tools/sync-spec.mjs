// Copies the frozen UI spec files into frontend/src/lib/spec/ui (owner P08). See src/lib/spec/README.md.
import { copyFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
for (const f of ["tokens.json", "module-registry.json"]) {
  copyFileSync(join(root, "docs/spec/ui", f), join(root, "frontend/src/lib/spec/ui", f));
  console.log(`sync-spec: frontend/src/lib/spec/ui/${f} <- docs/spec/ui/${f}`);
}
