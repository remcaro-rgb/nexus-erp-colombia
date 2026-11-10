"""UBL 2.1 XML for FE / NC / ND / DEE POS / DS / nómina, ApplicationResponse and AttachedDocument.

Pure and deterministic: the same model and inputs give byte-identical XML (golden files). Element
order follows the UBL 2.1 schemas of the DIAN toolbox v1.8 (``XSD/maindoc``); the DIAN extension
block (``sts:DianExtensions``) and the ``ext:UBLExtension`` that later receives the signature are
emitted empty-signed here and completed by :mod:`einvoice.signing`. Every document declares
``ProfileExecutionID`` = the environment (always ``2`` = pruebas in this DEV-only edition).
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from decimal import Decimal

from einvoice import annex
from einvoice.errors import present
from einvoice.model import FiscalDocumentModel, Party

NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "xades": "http://uri.etsi.org/01903/v1.3.2#",
}
ROOT_NS = {
    "Invoice": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "CreditNote": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "DebitNote": "urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2",
    "ApplicationResponse": "urn:oasis:names:specification:ubl:schema:xsd:ApplicationResponse-2",
    "AttachedDocument": "urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2",
    # Nómina: DIAN's own namespace (annex v1.0 not obtained; synthetic namespace label, B-06).
    "NominaIndividual": "dian:gov:co:facturaelectronica:NominaIndividual",
    "NominaIndividualDeAjuste": "dian:gov:co:facturaelectronica:NominaIndividualDeAjuste",
}
CUSTOMIZATION = {
    "FE": "10",
    "FE_EXPORT": "10",
    "FE_CONTINGENCY_DIAN": "10",
    "FE_CONTINGENCY_ISSUER": "10",
    "NC": "20",
    "ND": "30",
    "DEE_POS": "10",
    "DS": "10",
    "DS_ADJUSTMENT": "10",
}

for _prefix, _uri in NS.items():
    if _prefix:
        ET.register_namespace(_prefix, _uri)


def _q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _el(parent: ET.Element, prefix: str, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    node = ET.SubElement(parent, _q(prefix, tag), {k: v for k, v in attrs.items() if v != ""})
    if text is not None:
        node.text = text
    return node


def amount(value: Decimal) -> str:
    """Document amounts: two decimals (the document totals are already rounded by the issuer, D-001)."""
    return str(value.quantize(Decimal("0.01")))


def _party(parent: ET.Element, wrapper: str, party: Party) -> None:
    acc = _el(parent, "cac", wrapper)
    p = _el(acc, "cac", "Party")
    name = _el(p, "cac", "PartyName")
    _el(name, "cbc", "Name", party.name or party.id_number)
    tax = _el(p, "cac", "PartyTaxScheme")
    _el(tax, "cbc", "RegistrationName", party.name or party.id_number)
    _el(
        tax,
        "cbc",
        "CompanyID",
        party.id_number,
        schemeAgencyID="195",
        schemeID=party.check_digit,
        schemeName=party.id_type,
    )
    scheme = _el(tax, "cac", "TaxScheme")
    _el(scheme, "cbc", "ID", "01")
    _el(scheme, "cbc", "Name", "IVA")
    if party.email:
        contact = _el(p, "cac", "Contact")
        _el(contact, "cbc", "ElectronicMail", party.email)


def _extensions(root: ET.Element, model_number: str, software_id: str, qr_url: str) -> None:
    exts = _el(root, "ext", "UBLExtensions")
    dian = _el(_el(exts, "ext", "UBLExtension"), "ext", "ExtensionContent")
    d = _el(dian, "sts", "DianExtensions")
    provider = _el(d, "sts", "SoftwareProvider")
    _el(provider, "sts", "SoftwareID", software_id, schemeAgencyID="195")
    _el(d, "sts", "QRCode", qr_url)
    # Second extension: receives ds:Signature (einvoice.signing).
    _el(_el(exts, "ext", "UBLExtension"), "ext", "ExtensionContent")


def _head(root: ET.Element, model: FiscalDocumentModel, unique_code: str, environment: str, type_tag: str) -> None:
    _el(root, "cbc", "UBLVersionID", "UBL 2.1")
    _el(root, "cbc", "CustomizationID", CUSTOMIZATION.get(model.family, "10"))
    _el(root, "cbc", "ProfileID", "DIAN 2.1")
    _el(root, "cbc", "ProfileExecutionID", environment)
    _el(root, "cbc", "ID", model.number)
    from einvoice import hashing

    _el(root, "cbc", "UUID", unique_code, schemeID=environment, schemeName=hashing.scheme_name(model.hash_kind))
    _el(root, "cbc", "IssueDate", model.issue_date.isoformat())
    _el(root, "cbc", "IssueTime", model.issue_time)
    if type_tag:
        _el(root, "cbc", type_tag, model.type_code)
    for note in model.notes:
        _el(root, "cbc", "Note", note)
    if model.synthetic:
        _el(root, "cbc", "Note", "DOCUMENTO SINTÉTICO — SIMULADO, NO CERTIFICADO (DEV)")
    _el(root, "cbc", "DocumentCurrencyCode", model.currency)
    _el(root, "cbc", "LineCountNumeric", str(len(model.lines)))


def _taxes(root: ET.Element, model: FiscalDocumentModel) -> None:
    schemes = sorted({t.scheme for line in model.lines for t in line.taxes})
    for scheme in schemes:
        total = _el(root, "cac", "TaxTotal")
        _el(total, "cbc", "TaxAmount", amount(model.tax_total(scheme)), currencyID=model.currency)
        by_rate: dict[Decimal, tuple[Decimal, Decimal]] = {}
        for line in model.lines:
            for t in line.taxes:
                if t.scheme == scheme:
                    base, amt = by_rate.get(t.rate, (Decimal(0), Decimal(0)))
                    by_rate[t.rate] = (base + t.taxable, amt + t.amount)
        for rate in sorted(by_rate):
            base, amt = by_rate[rate]
            sub = _el(total, "cac", "TaxSubtotal")
            _el(sub, "cbc", "TaxableAmount", amount(base), currencyID=model.currency)
            _el(sub, "cbc", "TaxAmount", amount(amt), currencyID=model.currency)
            cat = _el(sub, "cac", "TaxCategory")
            _el(cat, "cbc", "Percent", str(rate.quantize(Decimal("0.01"))))
            sch = _el(cat, "cac", "TaxScheme")
            _el(sch, "cbc", "ID", scheme)
            _el(sch, "cbc", "Name", annex.TAX_SCHEMES[scheme].label)


def _totals(root: ET.Element, model: FiscalDocumentModel, tag: str) -> None:
    tot = _el(root, "cac", tag)
    c = model.currency
    _el(tot, "cbc", "LineExtensionAmount", amount(model.line_extension), currencyID=c)
    _el(tot, "cbc", "TaxExclusiveAmount", amount(model.tax_exclusive), currencyID=c)
    _el(tot, "cbc", "TaxInclusiveAmount", amount(model.tax_inclusive), currencyID=c)
    _el(tot, "cbc", "PayableAmount", amount(model.payable), currencyID=c)


def _lines(root: ET.Element, model: FiscalDocumentModel, line_tag: str, qty_tag: str) -> None:
    for i, line in enumerate(model.lines, 1):
        ln = _el(root, "cac", line_tag)
        _el(ln, "cbc", "ID", str(i))
        _el(ln, "cbc", qty_tag, str(line.quantity), unitCode=line.unit_code)
        _el(ln, "cbc", "LineExtensionAmount", amount(line.line_extension), currencyID=model.currency)
        for t in line.taxes:
            tt = _el(ln, "cac", "TaxTotal")
            _el(tt, "cbc", "TaxAmount", amount(t.amount), currencyID=model.currency)
            sub = _el(tt, "cac", "TaxSubtotal")
            _el(sub, "cbc", "TaxableAmount", amount(t.taxable), currencyID=model.currency)
            _el(sub, "cbc", "TaxAmount", amount(t.amount), currencyID=model.currency)
            cat = _el(sub, "cac", "TaxCategory")
            _el(cat, "cbc", "Percent", str(t.rate.quantize(Decimal("0.01"))))
            _el(_el(cat, "cac", "TaxScheme"), "cbc", "ID", t.scheme)
        item = _el(ln, "cac", "Item")
        _el(item, "cbc", "Description", line.description)
        price = _el(ln, "cac", "Price")
        _el(price, "cbc", "PriceAmount", str(line.unit_price), currencyID=model.currency)
        _el(price, "cbc", "BaseQuantity", "1", unitCode=line.unit_code)


def _billing_reference(root: ET.Element, model: FiscalDocumentModel) -> None:
    ref = model.billing_reference
    if ref is None:
        return
    br = _el(root, "cac", "BillingReference")
    inv = _el(br, "cac", "InvoiceDocumentReference")
    _el(inv, "cbc", "ID", ref.number)
    _el(inv, "cbc", "UUID", ref.unique_code, schemeName=ref.scheme_name)
    _el(inv, "cbc", "IssueDate", ref.issue_date.isoformat())


def _serialize(root: ET.Element) -> bytes:
    ET.indent(root, space=" ")
    return bytes(ET.tostring(root, encoding="UTF-8", xml_declaration=True))


def _root(tag: str) -> ET.Element:
    root = ET.Element(f"{{{ROOT_NS[tag]}}}{tag}")
    return root


def invoice(model: FiscalDocumentModel, unique_code: str, *, environment: str, software_id: str, qr_url: str) -> bytes:
    """FE (01/02/03/04), DEE POS and documento soporte (all ``Invoice`` roots)."""
    root = _root("Invoice")
    _extensions(root, model.number, software_id, qr_url)
    _head(root, model, unique_code, environment, "InvoiceTypeCode")
    _party(root, "AccountingSupplierParty", model.supplier)
    _party(root, "AccountingCustomerParty", model.customer)
    _taxes(root, model)
    _totals(root, model, "LegalMonetaryTotal")
    _lines(root, model, "InvoiceLine", "InvoicedQuantity")
    return _serialize(root)


def credit_note(
    model: FiscalDocumentModel, unique_code: str, *, environment: str, software_id: str, qr_url: str
) -> bytes:
    """NC (91) and nota de ajuste al documento soporte (``CreditNote`` root)."""
    root = _root("CreditNote")
    _extensions(root, model.number, software_id, qr_url)
    _head(root, model, unique_code, environment, "CreditNoteTypeCode")
    disc = _el(root, "cac", "DiscrepancyResponse")
    _el(disc, "cbc", "ReferenceID", model.billing_reference.number if model.billing_reference else "")
    _el(disc, "cbc", "ResponseCode", model.concept_code)
    _billing_reference(root, model)
    _party(root, "AccountingSupplierParty", model.supplier)
    _party(root, "AccountingCustomerParty", model.customer)
    _taxes(root, model)
    _totals(root, model, "LegalMonetaryTotal")
    _lines(root, model, "CreditNoteLine", "CreditedQuantity")
    return _serialize(root)


def debit_note(
    model: FiscalDocumentModel, unique_code: str, *, environment: str, software_id: str, qr_url: str
) -> bytes:
    root = _root("DebitNote")
    _extensions(root, model.number, software_id, qr_url)
    _head(root, model, unique_code, environment, "")
    disc = _el(root, "cac", "DiscrepancyResponse")
    _el(disc, "cbc", "ReferenceID", model.billing_reference.number if model.billing_reference else "")
    _el(disc, "cbc", "ResponseCode", model.concept_code)
    _billing_reference(root, model)
    _party(root, "AccountingSupplierParty", model.supplier)
    _party(root, "AccountingCustomerParty", model.customer)
    _taxes(root, model)
    _totals(root, model, "RequestedMonetaryTotal")
    _lines(root, model, "DebitNoteLine", "DebitedQuantity")
    return _serialize(root)


def nomina(model: FiscalDocumentModel, unique_code: str, *, environment: str, software_id: str, qr_url: str) -> bytes:
    """NominaIndividual / NominaIndividualDeAjuste (annex v1.0 not obtained: SYNTHETIC layout, B-06)."""
    payroll = present(model.payroll, "payroll totals")
    tag = model.root
    ns = ROOT_NS[tag]
    root = ET.Element(f"{{{ns}}}{tag}")

    def sub(parent: ET.Element, name: str, **attrs: str) -> ET.Element:
        return ET.SubElement(parent, f"{{{ns}}}{name}", attrs)

    exts = _el(root, "ext", "UBLExtensions")
    _el(_el(exts, "ext", "UBLExtension"), "ext", "ExtensionContent")
    sub(root, "Novedad", CUNENov="")
    sub(
        root,
        "Periodo",
        FechaLiquidacionInicio=payroll.period_start.isoformat(),
        FechaLiquidacionFin=payroll.period_end.isoformat(),
        FechaGen=model.issue_date.isoformat(),
    )
    sub(root, "NumeroSecuenciaXML", Numero=model.number)
    sub(
        root,
        "InformacionGeneral",
        Version="SINTETICO-NO-VERIFICADO",
        Ambiente=environment,
        TipoXML=model.type_code,
        CUNE=unique_code,
        EncripCUNE="CUNE-SHA384",
        FechaGen=model.issue_date.isoformat(),
        HoraGen=model.issue_time,
    )
    if model.billing_reference is not None:
        sub(
            root,
            "Predecesor",
            NumeroPred=model.billing_reference.number,
            CUNEPred=model.billing_reference.unique_code,
            FechaGenPred=model.billing_reference.issue_date.isoformat(),
        )
    sub(root, "Empleador", NIT=model.supplier.id_number, DV=model.supplier.check_digit, RazonSocial=model.supplier.name)
    sub(root, "Trabajador", NumeroDocumento=model.customer.id_number, TipoDocumento=model.customer.id_type)
    sub(root, "DevengadosTotal").text = amount(payroll.devengados)
    sub(root, "DeduccionesTotal").text = amount(payroll.deducciones)
    sub(root, "ComprobanteTotal").text = amount(payroll.total)
    if model.synthetic:
        sub(root, "Notas").text = "DOCUMENTO SINTÉTICO — SIMULADO, NO CERTIFICADO (DEV)"
    return _serialize(root)


def application_response(
    *,
    number: str,
    issue_date: dt.date,
    issue_time: str,
    unique_code: str,
    environment: str,
    sender: Party,
    receiver: Party,
    response_code: str,
    response_label: str,
    document_number: str,
    document_unique_code: str,
    document_type_code: str,
    claim_concept: str = "",
    software_id: str = "",
) -> bytes:
    """RADIAN event (``ApplicationResponse``)."""
    root = _root("ApplicationResponse")
    exts = _el(root, "ext", "UBLExtensions")
    d = _el(_el(_el(exts, "ext", "UBLExtension"), "ext", "ExtensionContent"), "sts", "DianExtensions")
    _el(_el(d, "sts", "SoftwareProvider"), "sts", "SoftwareID", software_id, schemeAgencyID="195")
    _el(_el(exts, "ext", "UBLExtension"), "ext", "ExtensionContent")
    _el(root, "cbc", "UBLVersionID", "UBL 2.1")
    _el(root, "cbc", "CustomizationID", "1")
    _el(root, "cbc", "ProfileID", "DIAN 2.1: ApplicationResponse de la Factura Electrónica de Venta")
    _el(root, "cbc", "ProfileExecutionID", environment)
    _el(root, "cbc", "ID", number)
    _el(root, "cbc", "UUID", unique_code, schemeID=environment, schemeName="CUDE-SHA384")
    _el(root, "cbc", "IssueDate", issue_date.isoformat())
    _el(root, "cbc", "IssueTime", issue_time)
    _party(root, "SenderParty", sender)
    _party(root, "ReceiverParty", receiver)
    resp = _el(root, "cac", "DocumentResponse")
    r = _el(resp, "cac", "Response")
    _el(r, "cbc", "ResponseCode", response_code, listID=claim_concept)
    _el(r, "cbc", "Description", response_label)
    ref = _el(resp, "cac", "DocumentReference")
    _el(ref, "cbc", "ID", document_number)
    _el(ref, "cbc", "UUID", document_unique_code, schemeName="CUFE-SHA384")
    _el(ref, "cbc", "DocumentTypeCode", document_type_code)
    return _serialize(root)


def attached_document(
    *,
    number: str,
    issue_date: dt.date,
    issue_time: str,
    sender: Party,
    receiver: Party,
    document_number: str,
    document_unique_code: str,
    signed_document: bytes,
    validation_response: bytes,
    environment: str,
) -> bytes:
    """Delivery container (``AttachedDocument``): the signed document + the validation response (CDATA-escaped)."""
    root = _root("AttachedDocument")
    _el(root, "cbc", "UBLVersionID", "UBL 2.1")
    _el(root, "cbc", "CustomizationID", "Documentos adjuntos")
    _el(root, "cbc", "ProfileID", "Factura Electrónica de Venta")
    _el(root, "cbc", "ProfileExecutionID", environment)
    _el(root, "cbc", "ID", number)
    _el(root, "cbc", "IssueDate", issue_date.isoformat())
    _el(root, "cbc", "IssueTime", issue_time)
    _el(root, "cbc", "DocumentType", "Contenedor de Factura Electrónica")
    _el(root, "cbc", "ParentDocumentID", document_number)
    _party(root, "SenderParty", sender)
    _party(root, "ReceiverParty", receiver)
    att = _el(_el(root, "cac", "Attachment"), "cac", "ExternalReference")
    _el(att, "cbc", "MimeCode", "text/xml")
    _el(att, "cbc", "EncodingCode", "UTF-8")
    _el(att, "cbc", "Description", signed_document.decode("utf-8"))
    line = _el(root, "cac", "ParentDocumentLineReference")
    _el(line, "cbc", "LineID", "1")
    ref = _el(line, "cac", "DocumentReference")
    _el(ref, "cbc", "ID", document_number)
    _el(ref, "cbc", "UUID", document_unique_code, schemeName="CUFE-SHA384")
    _el(ref, "cbc", "IssueDate", issue_date.isoformat())
    _el(ref, "cbc", "DocumentType", "ApplicationResponse")
    res = _el(_el(ref, "cac", "Attachment"), "cac", "ExternalReference")
    _el(res, "cbc", "MimeCode", "text/xml")
    _el(res, "cbc", "EncodingCode", "UTF-8")
    _el(res, "cbc", "Description", validation_response.decode("utf-8"))
    return _serialize(root)
