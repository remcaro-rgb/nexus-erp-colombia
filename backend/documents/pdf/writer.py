"""Minimal deterministic PDF 1.4 writer (D-005: the same template version + data always give the same bytes).

* No timestamps, no random ids: the trailer ``/ID`` is the SHA-256 of the page content, the Info dictionary only
  carries the producer and the title given by the caller.
* Text uses the standard-14 fonts with ``/WinAnsiEncoding`` (Windows-1252, the es-CO alphabet); content streams are
  not compressed (they stay searchable), images are 8-bit DeviceGray compressed with zlib level 9 (deterministic).
* Coordinates are points from the bottom-left corner (PDF user space); ``Page.top`` helpers measure from the top.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field
from decimal import Decimal

from documents.pdf.fonts import FONTS, text_width

PRODUCER = "Nexus ERP documents 1.0 (DEV)"
A4 = (Decimal("595.28"), Decimal("841.89"))
MM = Decimal("2.834645669")  # points per millimetre
FONT_KEYS = {name: f"F{n}" for n, name in enumerate(FONTS, start=1)}


def num(value: float | int | Decimal) -> str:
    """PDF number with at most 2 decimals and no exponent (deterministic textual form)."""
    q = Decimal(str(value)).quantize(Decimal("0.01"))
    text = format(q, "f").rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def encode_text(text: str) -> bytes:
    """A PDF literal string body in WinAnsi (cp1252); unknown glyphs become '?'."""
    raw = text.replace("\r", " ").replace("\n", " ").replace("\t", " ").encode("cp1252", errors="replace")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # ( ) \
            out += b"\\" + bytes([b])
        elif b < 32:
            out += f"\\{b:03o}".encode()
        else:
            out.append(b)
    return bytes(out)


@dataclass(slots=True)
class Image:
    width: int
    height: int
    pixels: bytes  # 8-bit gray rows, top to bottom


@dataclass
class Page:
    width: Decimal
    height: Decimal
    ops: list[bytes] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    fonts: set[str] = field(default_factory=set)

    def text(
        self, x: float | Decimal, y: float | Decimal, value: str, font: str = "Helvetica", size: float = 10
    ) -> None:
        if not value:
            return
        key = FONT_KEYS[font]
        self.fonts.add(font)
        self.ops.append(
            b"BT /" + key.encode() + b" " + num(size).encode() + b" Tf " + num(x).encode() + b" " + num(y).encode()
            + b" Td (" + encode_text(value) + b") Tj ET"
        )  # fmt: skip

    def text_right(
        self, right: float | Decimal, y: float | Decimal, value: str, font: str = "Helvetica", size: float = 10
    ) -> None:
        self.text(Decimal(str(right)) - Decimal(str(text_width(value, font, size))), y, value, font, size)

    def text_center(
        self, center: float | Decimal, y: float | Decimal, value: str, font: str = "Helvetica", size: float = 10
    ) -> None:
        half = Decimal(str(text_width(value, font, size))) / 2
        self.text(Decimal(str(center)) - half, y, value, font, size)

    def line(
        self, x1: float | Decimal, y1: float | Decimal, x2: float | Decimal, y2: float | Decimal, width: float = 0.5
    ) -> None:
        self.ops.append(f"{num(width)} w {num(x1)} {num(y1)} m {num(x2)} {num(y2)} l S".encode())

    def rect(
        self, x: float | Decimal, y: float | Decimal, w: float | Decimal, h: float | Decimal, *, fill: bool = False,
        gray: float = 0, width: float = 0.5,
    ) -> None:  # fmt: skip
        if fill:
            self.ops.append(f"{num(gray)} g {num(x)} {num(y)} {num(w)} {num(h)} re f 0 g".encode())
        else:
            self.ops.append(f"{num(width)} w {num(x)} {num(y)} {num(w)} {num(h)} re S".encode())

    def image(self, name: str, x: float | Decimal, y: float | Decimal, w: float | Decimal, h: float | Decimal) -> None:
        self.images.append(name)
        self.ops.append(f"q {num(w)} 0 0 {num(h)} {num(x)} {num(y)} cm /{name} Do Q".encode())

    def watermark(self, value: str, size: float = 9) -> None:
        """A visible label on the page (synthetic documents, D-005): top and bottom margins."""
        self.text_center(self.width / 2, self.height - 14, value, "Helvetica-Bold", size)
        self.text_center(self.width / 2, Decimal(8), value, "Helvetica-Bold", size)

    def content(self) -> bytes:
        return b"\n".join(self.ops)


class PdfDocument:
    """Collects pages and images; :meth:`build` serializes them (deterministic)."""

    def __init__(self, title: str = "") -> None:
        self.title = title
        self.pages: list[Page] = []
        self._images: dict[str, Image] = {}

    def page(self, width: Decimal = A4[0], height: Decimal = A4[1]) -> Page:
        p = Page(Decimal(width), Decimal(height))
        self.pages.append(p)
        return p

    def add_image(self, image: Image) -> str:
        """Register a grayscale image; identical pixels share one XObject. Returns its resource name."""
        digest = hashlib.sha256(f"{image.width}x{image.height}".encode() + image.pixels).hexdigest()[:16]
        name = f"Im{digest}"
        self._images.setdefault(name, image)
        return name

    def build(self) -> bytes:
        if not self.pages:
            self.page()
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # placeholder, filled below
        pages_ref = add(b"")
        font_refs = {
            name: add(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode()
            )
            for name in FONTS
        }  # fmt: skip
        image_refs: dict[str, int] = {}
        for name in sorted(self._images):
            img = self._images[name]
            data = zlib.compress(img.pixels, 9)
            image_refs[name] = add(
                f"<< /Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} /ColorSpace /DeviceGray "
                f"/BitsPerComponent 8 /Interpolate false /Filter /FlateDecode /Length {len(data)} >>\nstream\n".encode()
                + data + b"\nendstream"
            )  # fmt: skip
        page_refs: list[int] = []
        digest = hashlib.sha256()
        for p in self.pages:
            content = p.content()
            digest.update(content)
            content_ref = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
            fonts = " ".join(f"/{FONT_KEYS[f]} {font_refs[f]} 0 R" for f in FONTS)
            xobjects = " ".join(f"/{n} {image_refs[n]} 0 R" for n in sorted(set(p.images)))
            resources = f"<< /Font << {fonts} >>" + (f" /XObject << {xobjects} >>" if xobjects else "") + " >>"
            page_refs.append(
                add(
                    f"<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 {num(p.width)} {num(p.height)}] "
                    f"/Resources {resources} /Contents {content_ref} 0 R >>".encode()
                )
            )
        kids = " ".join(f"{r} 0 R" for r in page_refs)
        objects[pages_ref - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_refs)} >>".encode()
        objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_ref} 0 R >>".encode()
        info = add(b"<< /Producer (" + encode_text(PRODUCER) + b") /Title (" + encode_text(self.title) + b") >>")
        doc_id = digest.hexdigest()[:32]

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for n, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += str(n).encode() + b" 0 obj\n" + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R "
            f"/ID [<{doc_id}> <{doc_id}>] >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
        return bytes(out)
