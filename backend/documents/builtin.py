"""Built-in document templates (versioned in code; per-company overrides live in ``DocumentTemplate``).

Each template is the sandboxed markup of :mod:`documents.markup` + :mod:`documents.layout`. The version string is
part of the deterministic output (footer) and of the stored document hash, so a template change never silently
changes an archived PDF. ``inventory.label`` uses the dedicated label layout (:mod:`documents.labels`). Any other
template code renders with ``generic`` (a key/value listing of the plain data) so that every module's PDF route works.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuiltinTemplate:
    code: str
    name: str
    document_type: str
    version: str
    body: str
    sample: dict[str, object]


_COMPANY_HEADER = """# {{ company.name }}
> NIT {{ company.nit }}{% if company.dv %}-{{ company.dv }}{% endif %}{% if branch.name %} · Sucursal {{ branch.name }}{% endif %}
"""

_VOUCHER_BODY = (
    _COMPANY_HEADER
    + """## {{ title }} N.º {{ number|default:'(sin numerar)' }}
Fecha: {{ document_date|date }} · Estado: {{ status_label }}{% if posting_date %} · Contabilizado: {{ posting_date|date }}{% endif %}
{{ party_role }}: {{ party.name|default:'(sin tercero)' }}{% if party.tax_id %} · NIT/CC {{ party.tax_id }}{% endif %}
Fondo: {{ fund.code }} — {{ fund.name }} ({{ fund.kind }}) · Medio de pago: {{ payment_method_label }}{% if reference %} · Ref. {{ reference }}{% endif %}
---
@cols 7,43,20,15,15
|!Lín.|Concepto / documento|Tipo|Base|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.kind_label }}|{{ l.base|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
{% if withholding_total.amount %}|Retenciones|{{ withholding_total|money }}|
{% endif %}|!{{ total_label }}|{{ total|money }}|
**Valor en letras: {{ total|words }}**
{% if notes %}Observaciones: {{ notes }}
{% endif %}@space 18
@cols 50,50
|!Elaboró|Aprobó|
|{{ prepared_by.name }}|{{ approved_by_names|default:'(sin aprobación requerida)' }}|
> Asiento contable: {{ journal_entry|default:'—' }} · Moneda {{ currency }}
"""
)

_VOUCHER_SAMPLE: dict[str, object] = {
    "title": "RECIBO DE CAJA",
    "number": "RC-000001",
    "document_date": "2026-11-09",
    "status_label": "Contabilizado",
    "party_role": "Recibido de",
    "party": {"name": "Cliente Demo S.A.S. (SINTÉTICO)", "tax_id": "900100001-1"},
    "fund": {"code": "BCO-1", "name": "Banco sintético", "kind": "Banco"},
    "payment_method_label": "Transferencia",
    "company": {"name": "Empresa Demo S.A.S. (SINTÉTICA)", "nit": "900000001", "dv": "1"},
    "lines": [
        {"line_no": 1, "description": "Factura SETT-1001", "kind_label": "Abono cartera", "base": None,
         "amount": {"amount": "600000.0000", "currency": "COP"}},
    ],  # fmt: skip
    "withholding_total": {"amount": "0", "currency": "COP"},
    "total_label": "TOTAL RECIBIDO",
    "total": {"amount": "600000.0000", "currency": "COP"},
    "prepared_by": {"name": "Cajero demo"},
    "approved_by_names": "",
    "currency": "COP",
}

GENERIC_BODY = """# {{ title|default:document_type }}
> Documento generado por Nexus ERP (DEV) · plantilla genérica
---
@cols 35,65
{% for f in fields %}|{{ f.label }}|{{ f.value }}|
{% endfor %}{% for t in tables %}## {{ t.title }}
{% for r in t.rows %}|{{ r.0 }}|{{ r.1 }}|
{% endfor %}{% endfor %}"""

_SIMPLE_DOCS: dict[str, tuple[str, str, str]] = {
    # code: (name, document_type, body)
    "sales.quote": (
        "Cotización",
        "sales.quote",
        _COMPANY_HEADER
        + """## COTIZACIÓN N.º {{ number }}
Fecha: {{ document_date|date }} · Válida hasta: {{ valid_until|date }}
Cliente: {{ party.name }} · NIT/CC {{ party.tax_id }}
---
@cols 8,52,12,14,14
|!Lín.|Descripción|Cant.|Precio|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.quantity|number }}|{{ l.unit_price|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
|Subtotal|{{ subtotal|money }}|
|Impuestos|{{ tax_total|money }}|
|!TOTAL|{{ total|money }}|
**Valor en letras: {{ total|words }}**
""",
    ),
    "purchasing.order": (
        "Orden de compra",
        "purchasing.order",
        _COMPANY_HEADER
        + """## ORDEN DE COMPRA N.º {{ number }}
Fecha: {{ document_date|date }} · Proveedor: {{ party.name }} · NIT {{ party.tax_id }}
Entregar en: {{ warehouse.name }}
---
@cols 8,52,12,14,14
|!Lín.|Descripción|Cant.|Costo unit.|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.quantity|number }}|{{ l.unit_price|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
|!TOTAL|{{ total|money }}|
**Valor en letras: {{ total|words }}**
""",
    ),
    "sales.invoice": (
        "Representación gráfica de factura",
        "sales.invoice",
        _COMPANY_HEADER
        + """## FACTURA ELECTRÓNICA DE VENTA N.º {{ number }}
> {{ resolution_text|default:'Resolución de numeración: (SINTÉTICA — no válida ante la DIAN)' }}
Fecha: {{ issue_date|date }} · Vence: {{ due_date|date }}
Cliente: {{ party.name }} · NIT/CC {{ party.tax_id }}
---
@cols 8,52,12,14,14
|!Lín.|Descripción|Cant.|Precio|Valor|
{% for l in lines %}|{{ l.line_no }}|{{ l.description }}|{{ l.quantity|number }}|{{ l.unit_price|money }}|{{ l.amount|money }}|
{% endfor %}---
@cols 70,30
|Subtotal|{{ subtotal|money }}|
|Impuestos|{{ tax_total|money }}|
|!TOTAL|{{ total|money }}|
**Valor en letras: {{ total|words }}**
CUFE: {{ cufe|default:'SIN-CUFE (SINTÉTICO)' }}
""",
    ),
    "pos.ticket": (
        "Tirilla POS",
        "pos.ticket",
        """# {{ company.name }}
> NIT {{ company.nit }} · {{ branch.name }}
## DOCUMENTO POS N.º {{ number }}
Fecha: {{ document_date|date }} · Caja: {{ terminal }}
@cols 50,15,35
{% for l in lines %}|{{ l.description }}|{{ l.quantity|number:0 }}|{{ l.amount|money }}|
{% endfor %}@cols 65,35
|!TOTAL|{{ total|money }}|
> {{ total|words }}
""",
    ),
    "hr.certificate.labor": (
        "Certificado laboral",
        "hr.certificate",
        _COMPANY_HEADER
        + """## CERTIFICADO LABORAL
{{ city|default:'Bogotá D.C.' }}, {{ issue_date|date }}
La empresa {{ company.name }} certifica que {{ employee.name }}, identificado(a) con {{ employee.id_type }} {{ employee.id_number }}, labora en la compañía desde el {{ employee.start_date|date }} en el cargo de {{ employee.position }}, con contrato {{ employee.contract_type }}.
Se expide a solicitud del interesado.
@space 24
{{ signer.name }}
> {{ signer.position }}
""",
    ),
    "tax.certificate.withholding": (
        "Certificado de retención",
        "tax.certificate",
        _COMPANY_HEADER
        + """## CERTIFICADO DE RETENCIÓN {{ kind|upper }} — AÑO {{ year }}
Retenido: {{ party.name }} · NIT {{ party.tax_id }}
---
@cols 40,30,30
|!Concepto|Base|Retenido|
{% for l in lines %}|{{ l.concept }}|{{ l.base|money }}|{{ l.amount|money }}|
{% endfor %}---
**Total retenido: {{ total|money }} ({{ total|words }})**
> Certificado SINTÉTICO generado en DEV — no válido ante la DIAN.
""",
    ),
    "collection.letter": (
        "Carta de cobro",
        "sales.collection_letter",
        _COMPANY_HEADER
        + """## CARTA DE COBRO
{{ issue_date|date }}
Señores {{ party.name }}:
A la fecha registran un saldo vencido de {{ total|money }} ({{ total|words }}) correspondiente a:
@cols 30,25,20,25
|!Documento|Vencimiento|Días|Saldo|
{% for l in lines %}|{{ l.number }}|{{ l.due_date|date }}|{{ l.days }}|{{ l.balance|money }}|
{% endfor %}Agradecemos su pago oportuno.
""",
    ),
    "payroll.payslip": (
        "Desprendible de nómina",
        "payroll.payslip",
        _COMPANY_HEADER
        + """## DESPRENDIBLE DE NÓMINA — {{ period }}
Empleado: {{ employee.name }} · {{ employee.id_number }}
@cols 50,25,25
|!Concepto|Devengos|Deducciones|
{% for l in lines %}|{{ l.concept }}|{{ l.earning|money }}|{{ l.deduction|money }}|
{% endfor %}---
|!NETO A PAGAR|{{ net|money }}| |
> {{ net|words }}
""",
    ),
}

BUILTINS: dict[str, BuiltinTemplate] = {
    "treasury.receipt": BuiltinTemplate(
        "treasury.receipt", "Recibo de caja", "treasury.receipt", "1.0.0", _VOUCHER_BODY, _VOUCHER_SAMPLE
    ),
    "treasury.disbursement": BuiltinTemplate(
        "treasury.disbursement",
        "Comprobante de egreso",
        "treasury.disbursement",
        "1.0.0",
        _VOUCHER_BODY,
        {**_VOUCHER_SAMPLE, "title": "COMPROBANTE DE EGRESO", "number": "CE-000001", "party_role": "Pagado a",
         "total_label": "TOTAL PAGADO"},
    ),  # fmt: skip
    "generic": BuiltinTemplate("generic", "Documento genérico", "generic", "1.0.0", GENERIC_BODY, {}),
    **{
        code: BuiltinTemplate(code, name, doc_type, "1.0.0", body, {})
        for code, (name, doc_type, body) in _SIMPLE_DOCS.items()
    },
}

#: Codes with a dedicated layout (not markup).
LABEL_TEMPLATE = "inventory.label"
LABEL_VERSION = "1.0.0"


def builtin(code: str) -> BuiltinTemplate | None:
    return BUILTINS.get(code)


def catalogue() -> list[dict[str, str]]:
    rows = [{"code": t.code, "name": t.name, "document_type": t.document_type, "version": t.version}
            for t in BUILTINS.values()]  # fmt: skip
    rows.append({"code": LABEL_TEMPLATE, "name": "Etiqueta de código de barras", "document_type": "inventory.label",
                 "version": LABEL_VERSION})  # fmt: skip
    return sorted(rows, key=lambda r: r["code"])
