"""Minimal PDF reader for verification (tests, the documents' integrity check): objects through the xref table,
page text from ``Tj`` operators (WinAnsi), grayscale image XObjects (FlateDecode).

It reads what :mod:`documents.pdf.writer` produces (and other simple uncompressed PDFs); it is not a general parser.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

_OBJ = re.compile(rb"(\d+) 0 obj\n")
_INT = re.compile(rb"/(\w+) (\d+)(?: 0 R)?")
_TJ = re.compile(rb"\((?P<s>(?:\\.|[^\\)])*)\) Tj", re.DOTALL)
_REF = re.compile(rb"/Contents (\d+) 0 R")
_KIDS = re.compile(rb"/Kids \[([^\]]*)\]")


class PdfFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfObject:
    number: int
    head: bytes
    stream: bytes | None


@dataclass(frozen=True, slots=True)
class GrayImage:
    name: str
    width: int
    height: int
    pixels: bytes

    def row(self, y: int) -> bytes:
        return self.pixels[y * self.width : (y + 1) * self.width]


def _xref(data: bytes) -> list[int]:
    pos = data.rfind(b"startxref")
    if pos < 0:
        raise PdfFormatError("no startxref")
    start = int(data[pos + 9 :].split()[0])
    lines = data[start:].split(b"\n")
    if lines[0].strip() != b"xref":
        raise PdfFormatError("no xref table")
    count = int(lines[1].split()[1])
    return [int(line.split()[0]) for line in lines[3 : 2 + count]]


def objects(data: bytes) -> dict[int, PdfObject]:
    if not data.startswith(b"%PDF-"):
        raise PdfFormatError("not a PDF")
    out: dict[int, PdfObject] = {}
    for offset in _xref(data):
        m = _OBJ.match(data, offset)
        if m is None:
            raise PdfFormatError(f"no object at {offset}")
        number = int(m.group(1))
        body_start = m.end()
        stream_at = data.find(b">>\nstream\n", body_start)
        end_at = data.find(b"\nendobj", body_start)
        if 0 <= stream_at < end_at:
            head = data[body_start : stream_at + 2]
            lengths = dict((k, int(v)) for k, v in _INT.findall(head))
            length = lengths.get(b"Length")
            if length is None:
                raise PdfFormatError(f"stream object {number} without /Length")
            begin = stream_at + len(b">>\nstream\n")
            out[number] = PdfObject(number, head, data[begin : begin + length])
        else:
            out[number] = PdfObject(number, data[body_start:end_at], None)
    return out


def unescape(raw: bytes) -> str:
    out = bytearray()
    i = 0
    simple = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(raw):
        b = raw[i]
        if b != 0x5C:
            out.append(b)
            i += 1
            continue
        nxt = raw[i + 1]
        if 0x30 <= nxt <= 0x37:
            digits = raw[i + 1 : i + 4]
            j = 0
            while j < len(digits) and 0x30 <= digits[j] <= 0x37:
                j += 1
            out.append(int(digits[:j], 8) & 0xFF)
            i += 1 + j
        else:
            out.append(simple.get(nxt, nxt))
            i += 2
    return out.decode("cp1252", errors="replace")


def _pages(objs: dict[int, PdfObject]) -> list[PdfObject]:
    root = next(o for o in objs.values() if b"/Type /Pages" in o.head)
    kids = _KIDS.search(root.head)
    if kids is None:
        return []
    refs = [int(x) for x in kids.group(1).split() if x.isdigit() and x != b"0"]
    return [objs[r] for r in refs]


def page_texts(data: bytes) -> list[str]:
    """Text of each page, one ``Tj`` string per line, in drawing order."""
    objs = objects(data)
    texts: list[str] = []
    for page in _pages(objs):
        ref = _REF.search(page.head)
        content = objs[int(ref.group(1))].stream if ref else b""
        texts.append("\n".join(unescape(m.group("s")) for m in _TJ.finditer(content or b"")))
    return texts


def text(data: bytes) -> str:
    return "\n".join(page_texts(data))


def images(data: bytes) -> list[GrayImage]:
    out: list[GrayImage] = []
    for o in objects(data).values():
        if b"/Subtype /Image" not in o.head or o.stream is None:
            continue
        values = dict((k, int(v)) for k, v in _INT.findall(o.head))
        if b"/DeviceGray" not in o.head or values.get(b"BitsPerComponent") != 8:
            raise PdfFormatError("only 8-bit DeviceGray images are supported")
        pixels = zlib.decompress(o.stream) if b"/FlateDecode" in o.head else o.stream
        name = f"obj{o.number}"
        out.append(GrayImage(name, values[b"Width"], values[b"Height"], pixels))
    return out
