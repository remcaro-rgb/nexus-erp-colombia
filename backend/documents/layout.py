"""Document layout markup → PDF pages (and an HTML preview of the same blocks).

A rendered template (``documents.markup.render``) is a sequence of lines; a line's *template-authored* prefix
decides its block (interpolated values carry ``VALUE_MARK`` and are never markup):

====================  =========================================================================================
``# text``            title (Helvetica-Bold 15)
``## text``           heading (Helvetica-Bold 11)
``> text``            small print (8 pt)
``**text**``          bold paragraph
``---``               horizontal rule
``@cols 10,50,20,20`` column widths (percent) for the following table rows
``|a|b|c|``           table row (``|!a|b|`` = header row, bold, shaded); numeric cells are right-aligned
``@barcode VALUE``    Code 128 barcode image of VALUE (P09 encoder) with the human-readable text
``@space N``          vertical space of N points (≤ 200)
``@pagebreak``        new page
anything else         paragraph (wrapped)
====================  =========================================================================================

Pages are A4 with 40 pt margins; every page carries the watermark (synthetic documents, D-005) and the footer
"Página n de m · <template code> v<version>".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from decimal import Decimal

from documents.barcode import bars_image
from documents.markup import VALUE_MARK
from documents.pdf.fonts import fit, wrap
from documents.pdf.writer import A4, Page, PdfDocument

MARGIN = Decimal(40)
_NUMERIC = re.compile(r"^-?(\$ |[A-Z]{3} )?-?[\d.]+(,\d+)?$")
_COLS = re.compile(r"^@cols\s+(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*$")


@dataclass(frozen=True, slots=True)
class Block:
    kind: str  # title, heading, small, bold, para, rule, row, header, barcode, space, pagebreak
    text: str = ""
    cells: tuple[str, ...] = ()
    cols: tuple[int, ...] = ()
    amount: int = 0


def _strip(text: str) -> str:
    return text.replace(VALUE_MARK, "").strip()


def blocks(rendered: str) -> list[Block]:
    out: list[Block] = []
    cols: tuple[int, ...] = ()
    for raw in rendered.split("\n"):
        line = raw.rstrip()
        authored = line.lstrip(" ")
        if not _strip(line):
            continue
        if authored.startswith("## "):
            out.append(Block("heading", _strip(authored[3:])))
        elif authored.startswith("# "):
            out.append(Block("title", _strip(authored[2:])))
        elif authored.startswith("> "):
            out.append(Block("small", _strip(authored[2:])))
        elif authored.startswith("**") and authored.endswith("**") and len(authored) > 4:
            out.append(Block("bold", _strip(authored[2:-2])))
        elif authored == "---":
            out.append(Block("rule"))
        elif authored.startswith("@cols"):
            m = _COLS.match(authored)
            cols = tuple(int(x) for x in m.group(1).split(",")) if m else ()
        elif authored.startswith("@barcode "):
            out.append(Block("barcode", _strip(authored[len("@barcode ") :])))
        elif authored.startswith("@space"):
            parts = authored.split()
            amount = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 10
            out.append(Block("space", amount=min(amount, 200)))
        elif authored == "@pagebreak":
            out.append(Block("pagebreak"))
        elif authored.startswith("|") and authored.endswith("|") and len(authored) > 1:
            header = authored.startswith("|!")
            inner = authored[2:-1] if header else authored[1:-1]
            cells = tuple(_strip(c) for c in inner.split("|"))
            out.append(Block("header" if header else "row", cells=cells, cols=cols))
        else:
            out.append(Block("para", _strip(line)))
    return out


@dataclass
class _Cursor:
    doc: PdfDocument
    watermark: str | None
    page: Page = field(init=False)
    y: Decimal = field(init=False)

    def __post_init__(self) -> None:
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.page(*A4)
        if self.watermark:
            self.page.watermark(self.watermark)
        self.y = self.page.height - MARGIN - (12 if self.watermark else 0)

    @property
    def width(self) -> Decimal:
        return self.page.width - 2 * MARGIN

    def need(self, height: Decimal) -> None:
        if self.y - height < MARGIN + 14:
            self.new_page()


def _widths(cols: tuple[int, ...], n: int, total: Decimal) -> list[Decimal]:
    if len(cols) == n and sum(cols) > 0:
        return [total * c / sum(cols) for c in cols]
    return [total / n] * n


def _draw_row(cur: _Cursor, b: Block) -> None:
    size = 8.5
    font = "Helvetica-Bold" if b.kind == "header" else "Helvetica"
    widths = _widths(b.cols, len(b.cells), cur.width)
    wrapped = [wrap(c, font, size, max(w - 6, Decimal(10))) for c, w in zip(b.cells, widths, strict=True)]
    lines = max(len(w) for w in wrapped) or 1
    height = Decimal(str(lines * 11 + 4))
    cur.need(height)
    if b.kind == "header":
        cur.page.rect(MARGIN, cur.y - height + 2, cur.width, height, fill=True, gray=0.9)
    x = MARGIN
    for cell, w, cell_lines in zip(b.cells, widths, wrapped, strict=True):
        numeric = b.kind == "row" and bool(_NUMERIC.match(cell))
        for n, text in enumerate(cell_lines):
            y = cur.y - 9 - 11 * n
            if numeric:
                cur.page.text_right(x + w - 3, y, text, font, size)
            else:
                cur.page.text(x + 3, y, text, font, size)
        x += w
    cur.y -= height
    cur.page.line(MARGIN, cur.y + 2, MARGIN + cur.width, cur.y + 2, 0.25)


def _draw_barcode(cur: _Cursor, value: str) -> None:
    from inventory.barcodes.core import code128_modules

    modules = code128_modules(value)
    img = bars_image(modules)
    name = cur.doc.add_image(img)
    width = Decimal(img.width) * Decimal("0.75")
    height = Decimal(36)
    cur.need(height + 14)
    cur.page.image(name, MARGIN, cur.y - height, width, height)
    cur.page.text_center(MARGIN + width / 2, cur.y - height - 10, value, "Courier", 9)
    cur.y -= height + 16


def to_pdf(
    items: list[Block], *, title: str, watermark: str | None, footer: str, document: PdfDocument | None = None
) -> bytes:
    doc = document or PdfDocument(title)
    cur = _Cursor(doc, watermark)
    for b in items:
        if b.kind in ("title", "heading", "small", "bold", "para"):
            font, size, gap = {
                "title": ("Helvetica-Bold", 15, 20),
                "heading": ("Helvetica-Bold", 11, 15),
                "small": ("Helvetica", 8, 10),
                "bold": ("Helvetica-Bold", 9.5, 13),
                "para": ("Helvetica", 9.5, 13),
            }[b.kind]
            for text in wrap(b.text, font, size, cur.width):
                cur.need(Decimal(gap))
                cur.page.text(MARGIN, cur.y - Decimal(str(size)), text, font, size)
                cur.y -= Decimal(gap)
        elif b.kind == "rule":
            cur.need(Decimal(8))
            cur.page.line(MARGIN, cur.y - 4, MARGIN + cur.width, cur.y - 4, 0.75)
            cur.y -= 8
        elif b.kind in ("row", "header"):
            _draw_row(cur, b)
        elif b.kind == "barcode":
            _draw_barcode(cur, b.text)
        elif b.kind == "space":
            cur.y -= b.amount
        elif b.kind == "pagebreak":
            cur.new_page()
    total = len(doc.pages)
    for n, page in enumerate(doc.pages, start=1):
        text = fit(f"Página {n} de {total} · {footer}", "Helvetica", 7, page.width - 2 * MARGIN)
        page.text_right(page.width - MARGIN, Decimal(22), text, "Helvetica", 7)
    return doc.build()


def to_html(items: list[Block], *, title: str, watermark: str | None) -> str:
    """Accessible HTML preview (escaped) of the same blocks; tables get header cells and scope."""
    e = html.escape
    parts = [f'<article class="doc-preview" aria-label="{e(title)}">']
    if watermark:
        parts.append(f'<p class="doc-watermark" role="note">{e(watermark)}</p>')
    table: list[str] = []

    def flush() -> None:
        if table:
            parts.append('<table class="doc-table">' + "".join(table) + "</table>")
            table.clear()

    for b in items:
        if b.kind in ("row", "header"):
            tag = "th" if b.kind == "header" else "td"
            scope = ' scope="col"' if b.kind == "header" else ""
            table.append("<tr>" + "".join(f"<{tag}{scope}>{e(c)}</{tag}>" for c in b.cells) + "</tr>")
            continue
        flush()
        if b.kind == "title":
            parts.append(f"<h1>{e(b.text)}</h1>")
        elif b.kind == "heading":
            parts.append(f"<h2>{e(b.text)}</h2>")
        elif b.kind == "small":
            parts.append(f"<p><small>{e(b.text)}</small></p>")
        elif b.kind == "bold":
            parts.append(f"<p><strong>{e(b.text)}</strong></p>")
        elif b.kind == "para":
            parts.append(f"<p>{e(b.text)}</p>")
        elif b.kind == "rule":
            parts.append("<hr>")
        elif b.kind == "barcode":
            parts.append(f'<p class="doc-barcode" aria-label="Código de barras {e(b.text)}">{e(b.text)}</p>')
    flush()
    parts.append("</article>")
    return "".join(parts)
