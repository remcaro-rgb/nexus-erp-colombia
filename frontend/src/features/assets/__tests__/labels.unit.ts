import { describe, expect, it } from "vitest";
import { buildLabelsPdf, LABELS_PER_PAGE, pdfText, qrPayload } from "../labels";
import { labelItems } from "../api";
import { encodeQr } from "../qr";

describe("QR labels PDF", () => {
  it("escapes PDF strings and keeps WinAnsi accents", () => {
    expect(pdfText("Portátil (14)")).toBe("(Port\\341til \\(14\\))");
    expect(pdfText("Ñ€")).toBe("(\\321?)");
  });

  it("writes a valid xref and one QR per asset", () => {
    const items = Array.from({ length: LABELS_PER_PAGE + 1 }, (_, i) => ({ code: `LAP-${String(i).padStart(3, "0")}`, name: "Portátil (SINTÉTICO)", location: "Sede norte" }));
    const pdf = buildLabelsPdf(items);
    expect(pdf.startsWith("%PDF-1.4")).toBe(true);
    expect(pdf).toContain("/Count 2");
    const xrefAt = Number(/startxref\n(\d+)/.exec(pdf)![1]);
    expect(pdf.slice(xrefAt, xrefAt + 4)).toBe("xref");
    const offsets = [...pdf.slice(xrefAt).matchAll(/^(\d{10}) 00000 n $/gm)].map((m) => Number(m[1]));
    offsets.forEach((off, i) => expect(pdf.slice(off).startsWith(`${i + 1} 0 obj`)).toBe(true));
    for (const m of pdf.matchAll(/<< \/Length (\d+) >>\nstream\n/g)) {
      const start = m.index! + m[0].length;
      expect(pdf.slice(start + Number(m[1]), start + Number(m[1]) + 10)).toBe("\nendstream");
    }
    const darkFirst = encodeQr(qrPayload("LAP-000")).modules.flat().filter(Boolean).length;
    const firstPage = pdf.slice(pdf.indexOf("stream\n"), pdf.indexOf("endstream"));
    const firstLabel = firstPage.slice(0, firstPage.indexOf("(LAP-000)"));
    expect(firstLabel.match(/ re$/gm)!.length).toBe(darkFirst + 0);
    expect(() => buildLabelsPdf([])).toThrow(RangeError);
  });

  it("prints one label per non-disposed asset of the register (not per book)", () => {
    const report = {
      report: "assets.register",
      columns: [],
      rows: [
        { asset_id: "a", code: "B", name: "Silla", book: "NIIF", status: "ACTIVE", location: null },
        { asset_id: "a", code: "B", name: "Silla", book: "FISCAL", status: "ACTIVE", location: null },
        { asset_id: "c", code: "A", name: "Mesa", book: "NIIF", status: "DISPOSED", location: null },
      ],
    };
    expect(labelItems(report)).toEqual([{ code: "B", name: "Silla", location: null }]);
  });
});
