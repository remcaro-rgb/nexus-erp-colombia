// Asset tag labels as a PDF (owner P14): A4 sheet, 3 × 8 labels (63.5 × 33.9 mm), QR of the tag + tag + name.
// Minimal PDF 1.4 writer (vector modules, standard Helvetica fonts with WinAnsiEncoding); generated in the
// browser from the register report rows, so it shows exactly what the API returned (no static data).
import { encodeQr } from "./qr";

export interface LabelItem {
  code: string;
  name: string;
  location?: string | null;
}

const PAGE_W = 595.28;
const PAGE_H = 841.89;
const COLS = 3;
const ROWS = 8;
const LABEL_W = 180; // 63.5 mm
const LABEL_H = 96.1; // 33.9 mm
const MARGIN_X = (PAGE_W - COLS * LABEL_W) / 2;
const MARGIN_Y = (PAGE_H - ROWS * LABEL_H) / 2;
export const LABELS_PER_PAGE = COLS * ROWS;

/** QR payload of a tag (company-agnostic; scanning resolves it inside the active company). */
export function qrPayload(code: string): string {
  return `NXAF:${code}`;
}

/** PDF string literal in WinAnsi (Latin-1 subset): escapes `\ ( )`, octal for non-ASCII, `?` outside Latin-1. */
export function pdfText(text: string): string {
  let out = "";
  for (const ch of text) {
    const c = ch.codePointAt(0)!;
    if (ch === "\\" || ch === "(" || ch === ")") out += `\\${ch}`;
    else if (c >= 32 && c < 127) out += ch;
    else if (c >= 160 && c <= 255) out += `\\${c.toString(8).padStart(3, "0")}`;
    else out += "?";
  }
  return `(${out})`;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…`.replace("…", "...") : text;
}

function fmt(n: number): string {
  return Number(n.toFixed(2)).toString();
}

function labelOps(item: LabelItem, col: number, row: number): string {
  const x0 = MARGIN_X + col * LABEL_W;
  const y0 = PAGE_H - MARGIN_Y - (row + 1) * LABEL_H;
  const qr = encodeQr(qrPayload(item.code));
  const quiet = 2;
  const target = LABEL_H - 12;
  const cell = target / (qr.size + quiet * 2);
  const qx = x0 + 6 + quiet * cell;
  const qy = y0 + 6 + quiet * cell;
  const ops: string[] = ["q", "0.85 G 0.5 w", `${fmt(x0 + 2)} ${fmt(y0 + 2)} ${fmt(LABEL_W - 4)} ${fmt(LABEL_H - 4)} re S`, "0 g"];
  for (let y = 0; y < qr.size; y++)
    for (let x = 0; x < qr.size; x++)
      if (qr.modules[y]![x]) ops.push(`${fmt(qx + x * cell)} ${fmt(qy + (qr.size - 1 - y) * cell)} ${fmt(cell)} ${fmt(cell)} re`);
  ops.push("f");
  const tx = x0 + target + 12;
  ops.push("BT", `/F2 10 Tf ${fmt(tx)} ${fmt(y0 + LABEL_H - 26)} Td ${pdfText(truncate(item.code, 14))} Tj`, "ET");
  ops.push("BT", `/F1 7 Tf ${fmt(tx)} ${fmt(y0 + LABEL_H - 40)} Td ${pdfText(truncate(item.name, 22))} Tj`, "ET");
  if (item.location) ops.push("BT", `/F1 6 Tf ${fmt(tx)} ${fmt(y0 + LABEL_H - 52)} Td ${pdfText(truncate(item.location, 28))} Tj`, "ET");
  ops.push("BT", `/F1 5 Tf ${fmt(tx)} ${fmt(y0 + 10)} Td ${pdfText("Activo fijo - datos SINTETICOS (DEV)")} Tj`, "ET", "Q");
  return ops.join("\n");
}

/** Builds the PDF document (ASCII only, so string length = byte length). */
export function buildLabelsPdf(items: readonly LabelItem[]): string {
  if (!items.length) throw new RangeError("no labels to print");
  const pages: string[] = [];
  for (let start = 0; start < items.length; start += LABELS_PER_PAGE) {
    pages.push(
      items
        .slice(start, start + LABELS_PER_PAGE)
        .map((it, i) => labelOps(it, i % COLS, Math.floor(i / COLS)))
        .join("\n"),
    );
  }
  const objects: string[] = [];
  const pageIds = pages.map((_, i) => 5 + i * 2);
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pages.length} >>`;
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>";
  objects[4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>";
  pages.forEach((content, i) => {
    const id = pageIds[i]!;
    objects[id] =
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${fmt(PAGE_W)} ${fmt(PAGE_H)}] ` +
      `/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${id + 1} 0 R >>`;
    objects[id + 1] = `<< /Length ${content.length} >>\nstream\n${content}\nendstream`;
  });
  let pdf = "%PDF-1.4\n";
  const offsets: number[] = [];
  for (let id = 1; id < objects.length; id++) {
    offsets[id] = pdf.length;
    pdf += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id++) pdf += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return pdf;
}

export function labelsBlob(items: readonly LabelItem[]): Blob {
  const pdf = buildLabelsPdf(items);
  const bytes = new Uint8Array(pdf.length);
  for (let i = 0; i < pdf.length; i++) bytes[i] = pdf.charCodeAt(i) & 0xff;
  return new Blob([bytes], { type: "application/pdf" });
}
