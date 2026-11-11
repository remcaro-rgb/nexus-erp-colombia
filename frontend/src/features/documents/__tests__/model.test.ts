import { describe, expect, it } from "vitest";
import { etagFor, precheck, previewDocument } from "../model";

describe("template editor model", () => {
  it("pre-checks the sandboxed syntax like the server", () => {
    expect(precheck("# {{ company.name }}\n{% for l in lines %}{{ l.amount|money }}{% endfor %}")).toBeNull();
    expect(precheck("{{ ''.__class__ }}")).toMatch(/no permitida/);
    expect(precheck("{{ x._secret }}")).toMatch(/no permitida/);
    expect(precheck("{% for l in lines %}")).toMatch(/Falta cerrar/);
    expect(precheck("{% endif %}")).toMatch(/sin abrir/);
    expect(precheck("{% import os %}")).toMatch(/desconocida/);
  });

  it("wraps previews with a no-script CSP", () => {
    expect(previewDocument("<p>x</p>")).toContain("default-src 'none'");
  });

  it("computes the server's weak ETag", async () => {
    const tag = await etagFor("documents.DocumentTemplate", "0190a000-0000-7000-8000-000000000001", 2);
    expect(tag).toMatch(/^W\/"[0-9a-f]{32}"$/);
    expect(await etagFor("documents.DocumentTemplate", "0190a000-0000-7000-8000-000000000001", 2)).toBe(tag);
  });
});
