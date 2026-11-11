"""Synthetic fiscal downloads of the customer portal (T-PORTAL-CUS-05): invoice / credit-note PDF and XML,
statement PDF / CSV.

DEV-only edition: there are no real DIAN documents. Every artefact carries the label
``SINTÉTICO — sin validez fiscal`` (:data:`sales.portal.service.FISCAL_LABEL`):

* PDF — through the P17 ``DocumentRenderer`` when one is registered (label passed as ``watermark``), else a
  minimal self-contained PDF 1.4 (Helvetica, WinAnsi) with the label as the first and the last text line and in
  the document info (``/Subject``);
* XML — a UBL-2.1-*shaped* document (``NOT LEGALLY VERIFIED``: not the DIAN Anexo Técnico layout, which is an
  EXTERNAL input) with the label in a comment and in ``cbc:Note``; the CUFE comes from P15's
  ``FiscalDocuments`` read side when available, else it is ``SIN-CUFE (SINTÉTICO)``.

Only customer-facing data is rendered: number, dates, parties, line description / quantity / unit price / amount,
taxes and totals — never costs, margins, price sources, snapshots or formulas (T-PORTAL-CUS-04).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from kernel import services
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.documents import DocumentRenderer
from kernel.interfaces.fiscal import FiscalDocuments
from kernel.money import q_amount
from sales.models import Invoice, Party, SalesNote
from sales.portal.service import FISCAL_LABEL

NOT_VERIFIED = "Layout sintético — NOT LEGALLY VERIFIED (no es el Anexo Técnico DIAN)"
UBL = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CN = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"


def _amt(value: Decimal | None) -> str:
    return f"{q_amount(value or Decimal(0)):f}"


def _company_name(doc: Any) -> tuple[str, str]:
    from iam.models import Company

    row = Company.objects.filter(tenant_id=doc.tenant_id, id=doc.company_id).values("legal_name", "nit", "dv").first()
    if row is None:
        return "", ""
    return str(row["legal_name"]), f"{row['nit']}-{row['dv']}" if row.get("dv") else str(row["nit"])


def _party(doc: Any) -> Party:
    return Party.objects.get(tenant_id=doc.tenant_id, company_id=doc.company_id, id=doc.party_id)


def _cufe(doc: Any, doc_type: str) -> str:
    fiscal = services.optional(FiscalDocuments)
    if fiscal is not None:
        try:
            info = fiscal.status(doc.company_id, DocumentRef(doc_type, doc.id, doc.number))
        except Exception:  # noqa: BLE001 - the read side is informative here; the label is what matters
            info = None
        if info is not None and info.unique_code:
            return f"{info.unique_code} (SINTÉTICO)" if info.simulated else str(info.unique_code)
    return "SIN-CUFE (SINTÉTICO)"


# --------------------------------------------------------------------------- data of a document


def invoice_data(inv: Invoice) -> dict[str, Any]:
    seller, seller_nit = _company_name(inv)
    party = _party(inv)
    lines = [
        {
            "line_no": ln.line_no,
            "description": ln.description or "Ítem",
            "quantity": f"{ln.quantity.normalize():f}",
            "uom": ln.uom,
            "unit_price": f"{ln.unit_price:f}",
            "discount_pct": f"{ln.discount_pct.normalize():f}",
            "amount": _amt(ln.amount),
            "tax_code": ln.tax_code,
        }
        for ln in inv.lines.order_by("line_no")
    ]
    return {
        "kind": "FACTURA ELECTRÓNICA DE VENTA",
        "doc_type": "sales.invoice",
        "number": inv.number,
        "issue_date": inv.document_date.isoformat(),
        "due_date": inv.due_date.isoformat() if inv.due_date else "",
        "currency": inv.currency,
        "seller": seller,
        "seller_nit": seller_nit,
        "customer": party.legal_name,
        "customer_nit": f"{party.nit}-{party.dv}" if party.dv else party.nit,
        "lines": lines,
        "subtotal": _amt(inv.subtotal),
        "tax_total": _amt(inv.tax_total),
        "total": _amt(inv.total),
        "cufe": _cufe(inv, "sales.invoice"),
        "reference": "",
    }


def note_data(note: SalesNote) -> dict[str, Any]:
    seller, seller_nit = _company_name(note)
    party = _party(note)
    lines = [
        {
            "line_no": ln.line_no,
            "description": ln.description or "Ajuste",
            "quantity": f"{ln.quantity.normalize():f}",
            "uom": "",
            "unit_price": "",
            "discount_pct": "0",
            "amount": _amt(ln.amount),
            "tax_code": ln.tax_code,
        }
        for ln in note.lines.order_by("line_no")
    ]
    invoice_number = Invoice.objects.filter(id=note.invoice_id).values_list("number", flat=True).first() or ""
    return {
        "kind": "NOTA CRÉDITO",
        "doc_type": "sales.credit_note",
        "number": note.number,
        "issue_date": note.document_date.isoformat(),
        "due_date": "",
        "currency": note.currency,
        "seller": seller,
        "seller_nit": seller_nit,
        "customer": party.legal_name,
        "customer_nit": f"{party.nit}-{party.dv}" if party.dv else party.nit,
        "lines": lines,
        "subtotal": _amt(note.subtotal),
        "tax_total": _amt(note.tax_total),
        "total": _amt(note.total),
        "cufe": _cufe(note, "sales.credit_note"),
        "reference": invoice_number,
    }


# --------------------------------------------------------------------------- PDF


def _pdf_text(value: str) -> bytes:
    raw = value.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def minimal_pdf(title: str, lines: Sequence[str]) -> bytes:
    """A valid single-/multi-page PDF 1.4 with the given text lines (deterministic, no dependency)."""
    per_page = 48
    pages = [list(lines[i : i + per_page]) for i in range(0, max(len(lines), 1), per_page)] or [[]]
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # placeholder index computed below
    page_ids: list[int] = []
    for chunk in pages:
        stream = [b"BT", b"/F1 9 Tf", b"40 800 Td", b"12 TL"]
        for n, text in enumerate(chunk):
            stream.append((b"" if n == 0 else b"T* ") + b"(" + _pdf_text(text) + b") Tj")
        stream.append(b"ET")
        data = b"\n".join(stream)
        content = add(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
        page_ids.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_id).encode()
                + b" 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
                + str(font).encode()
                + b" 0 R >> >> /Contents "
                + str(content).encode()
                + b" 0 R >>"
            )
        )
    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    actual_pages = add(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>")
    assert actual_pages == pages_id  # noqa: S101 - layout invariant of this tiny writer
    info = add(
        b"<< /Title ("
        + _pdf_text(title)
        + b") /Subject ("
        + _pdf_text(FISCAL_LABEL)
        + b") /Producer (Nexus ERP DEV) >>"
    )
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(str(n).encode() + b" 0 obj\n" + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(catalog).encode()
        + b" 0 R /Info "
        + str(info).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return out.getvalue()


def _render(template: str, title: str, data: dict[str, Any], lines: list[str]) -> bytes:
    renderer = services.optional(DocumentRenderer)
    if renderer is not None:
        doc = renderer.render(template, data, watermark=FISCAL_LABEL)
        if FISCAL_LABEL.encode("utf-8") in doc.content or FISCAL_LABEL.encode("cp1252") in doc.content:
            return doc.content
        # a renderer that drops the watermark is not acceptable for a synthetic fiscal document: fall back
    return minimal_pdf(title, lines)


def document_pdf(data: dict[str, Any]) -> bytes:
    text = [
        FISCAL_LABEL,
        f"{data['kind']} N.º {data['number']}",
        f"Emisor: {data['seller']} NIT {data['seller_nit']}",
        f"Cliente: {data['customer']} NIT {data['customer_nit']}",
        f"Fecha: {data['issue_date']}" + (f"   Vence: {data['due_date']}" if data["due_date"] else ""),
        *([f"Factura de referencia: {data['reference']}"] if data["reference"] else []),
        f"CUFE/CUDE: {data['cufe']}",
        "",
        "Lín.  Descripción  Cantidad  Precio unitario  Valor",
    ]
    for ln in data["lines"]:
        text.append(
            f"{ln['line_no']}  {ln['description']}  {ln['quantity']} {ln['uom']}  {ln['unit_price']}  {ln['amount']}"
        )
    text += [
        "",
        f"Subtotal: {data['subtotal']} {data['currency']}",
        f"Impuestos: {data['tax_total']} {data['currency']}",
        f"Total: {data['total']} {data['currency']}",
        NOT_VERIFIED,
        FISCAL_LABEL,
    ]
    return _render(f"sales.portal.{data['doc_type']}", f"{data['kind']} {data['number']}", data, text)


# --------------------------------------------------------------------------- XML


def document_xml(data: dict[str, Any]) -> bytes:
    """Serialized by hand (no ``ElementTree.register_namespace``: that registry is process-global and would change
    the prefixes of every other XML the process writes, e.g. P15's golden documents)."""
    is_note = data["doc_type"] == "sales.credit_note"
    root_tag = "CreditNote" if is_note else "Invoice"
    cur = data["currency"]
    out: list[str] = []

    def cbc(name: str, text: str, indent: int = 1, **attrs: str) -> None:
        extra = "".join(f" {k}={quoteattr(v)}" for k, v in attrs.items())
        out.append(f"{'  ' * indent}<cbc:{name}{extra}>{escape(text)}</cbc:{name}>")

    out.append(f'<{root_tag} xmlns="{UBL_CN if is_note else UBL}" xmlns:cac="{CAC}" xmlns:cbc="{CBC}">')
    cbc("UBLVersionID", "UBL 2.1")
    cbc("ProfileID", "SINTETICO-NEXUS-DEV")
    cbc("ID", data["number"])
    cbc("UUID", data["cufe"], schemeName="CUFE-SINTETICO")
    cbc("IssueDate", data["issue_date"])
    if data["due_date"]:
        cbc("DueDate", data["due_date"])
    cbc("Note", FISCAL_LABEL)
    cbc("Note", NOT_VERIFIED)
    cbc("DocumentCurrencyCode", cur)
    if data["reference"]:
        out.append("  <cac:BillingReference><cac:InvoiceDocumentReference>")
        cbc("ID", data["reference"], 2)
        out.append("  </cac:InvoiceDocumentReference></cac:BillingReference>")
    for tag, name, nit in (
        ("AccountingSupplierParty", data["seller"], data["seller_nit"]),
        ("AccountingCustomerParty", data["customer"], data["customer_nit"]),
    ):
        out.append(f"  <cac:{tag}><cac:Party><cac:PartyTaxScheme>")
        cbc("RegistrationName", name, 2)
        cbc("CompanyID", nit, 2, schemeName="NIT")
        out.append(f"  </cac:PartyTaxScheme></cac:Party></cac:{tag}>")
    out.append("  <cac:TaxTotal>")
    cbc("TaxAmount", data["tax_total"], 2, currencyID=cur)
    out.append("  </cac:TaxTotal>")
    out.append("  <cac:LegalMonetaryTotal>")
    cbc("LineExtensionAmount", data["subtotal"], 2, currencyID=cur)
    cbc("PayableAmount", data["total"], 2, currencyID=cur)
    out.append("  </cac:LegalMonetaryTotal>")
    line_tag = "CreditNoteLine" if is_note else "InvoiceLine"
    for ln in data["lines"]:
        out.append(f"  <cac:{line_tag}>")
        cbc("ID", str(ln["line_no"]), 2)
        cbc("CreditedQuantity" if is_note else "InvoicedQuantity", ln["quantity"] or "0", 2)
        cbc("LineExtensionAmount", ln["amount"], 2, currencyID=cur)
        out.append("    <cac:Item>")
        cbc("Description", ln["description"], 3)
        out.append("    </cac:Item>")
        if ln["unit_price"]:
            out.append("    <cac:Price>")
            cbc("PriceAmount", ln["unit_price"], 3, currencyID=cur)
            out.append("    </cac:Price>")
        out.append(f"  </cac:{line_tag}>")
    out.append(f"</{root_tag}>")
    head = f'<?xml version="1.0" encoding="UTF-8"?>\n<!-- {FISCAL_LABEL} · {NOT_VERIFIED} -->\n'
    return (head + "\n".join(out) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- statement exports


def statement_csv(report: dict[str, Any]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([f"# {FISCAL_LABEL}"])
    w.writerow([c["label"] for c in report["columns"]])
    for r in report["rows"]:
        w.writerow([r[c["key"]] for c in report["columns"]])
    t = report["totals"]
    w.writerow(["Saldo inicial", "", "", "", "", t["opening_balance"]])
    w.writerow(["Totales", "", "", t["debit"], t["credit"], t["closing_balance"]])
    return ("﻿" + out.getvalue()).encode("utf-8")


def statement_pdf(report: dict[str, Any], customer: str) -> bytes:
    t = report["totals"]
    p = report["parameters"]
    text: list[str] = [
        FISCAL_LABEL,
        f"ESTADO DE CUENTA — {customer}",
        f"Periodo: {p['from']} a {p['to']} (COP)",
        f"Saldo inicial: {t['opening_balance']}",
        "",
        "Fecha  Documento  Cargos  Abonos  Saldo",
    ]
    text += [f"{r['date']}  {r['number']}  {r['debit']}  {r['credit']}  {r['balance']}" for r in report["rows"]]
    text += ["", f"Saldo final: {t['closing_balance']}", *_aging_lines(t.get("aging") or {}), FISCAL_LABEL]
    return _render("sales.portal.statement", f"Estado de cuenta {customer}", report, text)


def _aging_lines(aging: dict[str, str]) -> Iterable[str]:
    if aging:
        yield "Antigüedad de saldos: " + ", ".join(f"{k}: {v}" for k, v in aging.items())


__all__ = ["document_pdf", "document_xml", "invoice_data", "minimal_pdf", "note_data", "statement_csv", "statement_pdf"]
