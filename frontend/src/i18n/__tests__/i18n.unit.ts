// T-P08-08 i18n lint: es-CO default + en-US secondary with identical keys and placeholders; ICU formatting;
// no hard-coded user-visible strings in JSX of the shell and design system (text must come from messages).
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import ts from "typescript";
import { join } from "node:path";
import { esCO, enUS, translate } from "../index";
import { formatIcu } from "../icu";

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();

describe("T-P08-08 i18n", () => {
  it("T-P08-08 catalogs have the same keys and placeholders", () => {
    expect(Object.keys(enUS).sort()).toEqual(Object.keys(esCO).sort());
    for (const k of Object.keys(esCO) as Array<keyof typeof esCO>) expect(placeholders(enUS[k]), k).toEqual(placeholders(esCO[k]));
  });

  it("T-P08-08 ICU plural/select and es-CO number formatting", () => {
    expect(translate("es-CO", "header.unread", { count: 0 })).toBe("sin notificaciones nuevas");
    expect(translate("es-CO", "header.unread", { count: 1 })).toBe("1 notificación sin leer");
    expect(translate("es-CO", "grid.rows", { count: 10000 })).toBe("10.000 registros");
    expect(translate("en-US", "grid.rows", { count: 10000 })).toBe("10,000 records");
    expect(formatIcu("{g, select, f {ella} other {elle}}", { g: "f" })).toBe("ella");
  });

  it("T-P08-08 no hard-coded user-visible strings in shell/design-system JSX", () => {
    const roots = ["src/shell", "src/design-system", "src/app"].map((r) => join(process.cwd(), r));
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => (statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]));
    const offenders: string[] = [];
    const ATTRS = new Set(["aria-label", "title", "placeholder", "alt", "label"]);
    for (const f of roots.flatMap(walk).filter((x) => x.endsWith(".tsx") && !x.includes("__tests__") && !x.includes("/test/"))) {
      const sf = ts.createSourceFile(f, readFileSync(f, "utf-8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const visit = (n: ts.Node) => {
        if (ts.isJsxText(n) && /[A-Za-zÁÉÍÓÚáéíóúÑñ]{2,}/.test(n.getText())) offenders.push(`${f}: text "${n.getText().trim()}"`);
        if (ts.isJsxAttribute(n) && ATTRS.has(n.name.getText()) && n.initializer && ts.isStringLiteral(n.initializer) && /[A-Za-z]{2,}/.test(n.initializer.text)) {
          offenders.push(`${f}: ${n.name.getText()}="${n.initializer.text}"`);
        }
        ts.forEachChild(n, visit);
      };
      visit(sf);
    }
    expect(offenders).toEqual([]);
  });
});
