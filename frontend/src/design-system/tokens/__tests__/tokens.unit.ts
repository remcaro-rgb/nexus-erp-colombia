// Tokens -> CSS variables (P08): every tokens.json colour/layout value becomes a variable; static CSS has light,
// dark (class) and OS-preference blocks; no remote font or CDN in index.html / styles.
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cssVariables, tokens, tokensToCss, colorVar } from "../index";

describe("P08 tokens", () => {
  it("maps every colour of both themes and the layout sizes", () => {
    for (const theme of ["light", "dark"] as const) {
      const vars = cssVariables(tokens, theme);
      for (const [k, v] of Object.entries(tokens.color[theme])) expect(vars[colorVar(k)]).toBe(v);
    }
    const v = cssVariables(tokens, "light");
    expect(v["--nx-header-h"]).toBe("64px");
    expect(v["--nx-module-bar-h"]).toBe("48px");
    expect(v["--nx-sidebar-w"]).toBe("256px");
    expect(v["--nx-sidebar-collapsed-w"]).toBe("80px");
    expect(v["--nx-ai-panel-w"]).toBe("320px");
    expect(v["--nx-control-radius"]).toBe("12px");
    expect(v["--nx-font-family"]?.startsWith('"Inter"')).toBe(true);
  });

  it("static CSS has light, dark and reduced-motion blocks", () => {
    const css = tokensToCss(tokens);
    expect(css).toContain(":root {");
    expect(css).toContain(":root.dark {");
    expect(css).toContain("prefers-color-scheme: dark");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toContain("--nx-color-app-bg: #020617;");
  });

  it("no CDN or remote fonts", () => {
    for (const f of ["index.html", "src/design-system/styles.css", "src/app/main.tsx"]) {
      const src = readFileSync(join(process.cwd(), f), "utf-8");
      expect(src, f).not.toMatch(/https?:\/\/|fonts\.googleapis|cdn\./);
    }
  });
});
