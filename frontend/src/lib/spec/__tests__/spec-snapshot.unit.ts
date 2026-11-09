import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../../../..");

describe("frozen UI spec snapshot (src/lib/spec/ui) matches docs/spec/ui", () => {
  it.each(["tokens.json", "module-registry.json"])("%s is byte-identical (run `pnpm --dir frontend run sync-spec`)", (f) => {
    const snapshot = readFileSync(resolve(here, "../ui", f), "utf-8");
    const source = readFileSync(resolve(repoRoot, "docs/spec/ui", f), "utf-8");
    expect(snapshot).toBe(source);
  });
});
