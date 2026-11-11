"""Built-in document templates (versioned in code; per-company overrides live in ``DocumentTemplate``).

The bodies are the sandboxed markup of :mod:`documents.markup` + :mod:`documents.layout`, stored next to this module
in ``templates/<name>.tpl`` (line-based markup). The version string is part of the deterministic output (footer)
and of the stored document hash, so a template change never silently changes an archived PDF: change a body →
bump its version. ``inventory.label`` uses the dedicated label layout (:mod:`documents.labels`). Any other template
code renders with ``generic`` (a key/value listing of the plain data) so that every module's PDF route works.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True, slots=True)
class BuiltinTemplate:
    code: str
    name: str
    document_type: str
    version: str
    file: str
    sample: dict[str, object] = field(default_factory=dict)

    @property
    def body(self) -> str:
        return _read(self.file)


@functools.cache
def _read(name: str) -> str:
    return (TEMPLATES_DIR / f"{name}.tpl").read_text(encoding="utf-8")


_MONEY_600K = {"amount": "600000.0000", "currency": "COP"}
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
        {
            "line_no": 1,
            "description": "Factura SETT-1001",
            "kind_label": "Abono cartera",
            "base": None,
            "amount": _MONEY_600K,
        },
    ],
    "withholding_total": None,
    "total_label": "TOTAL RECIBIDO",
    "total": _MONEY_600K,
    "prepared_by": {"name": "Cajero demo"},
    "approved_by_names": "",
    "currency": "COP",
}
_CE_SAMPLE: dict[str, object] = {
    **_VOUCHER_SAMPLE,
    "title": "COMPROBANTE DE EGRESO",
    "number": "CE-000001",
    "party_role": "Pagado a",
    "total_label": "TOTAL PAGADO",
}

_SIMPLE: tuple[tuple[str, str, str], ...] = (
    # (code, name, document type) — body in templates/<code>.tpl
    ("sales.quote", "Cotización", "sales.quote"),
    ("purchasing.order", "Orden de compra", "purchasing.order"),
    ("sales.invoice", "Representación gráfica de factura", "sales.invoice"),
    ("pos.ticket", "Tirilla POS", "pos.ticket"),
    ("hr.certificate.labor", "Certificado laboral", "hr.certificate"),
    ("tax.certificate.withholding", "Certificado de retención", "tax.certificate"),
    ("collection.letter", "Carta de cobro", "sales.collection_letter"),
    ("payroll.payslip", "Desprendible de nómina", "payroll.payslip"),
)

BUILTINS: dict[str, BuiltinTemplate] = {
    "treasury.receipt": BuiltinTemplate(
        "treasury.receipt", "Recibo de caja", "treasury.receipt", "1.0.0", "treasury.voucher", _VOUCHER_SAMPLE
    ),
    "treasury.disbursement": BuiltinTemplate(
        "treasury.disbursement",
        "Comprobante de egreso",
        "treasury.disbursement",
        "1.0.0",
        "treasury.voucher",
        _CE_SAMPLE,
    ),
    "generic": BuiltinTemplate("generic", "Documento genérico", "generic", "1.0.0", "generic"),
    **{code: BuiltinTemplate(code, name, doc_type, "1.0.0", code) for code, name, doc_type in _SIMPLE},
}

#: Codes with a dedicated layout (not markup).
LABEL_TEMPLATE = "inventory.label"
LABEL_VERSION = "1.0.0"


def builtin(code: str) -> BuiltinTemplate | None:
    return BUILTINS.get(code)


def catalogue() -> list[dict[str, str]]:
    rows = [
        {"code": t.code, "name": t.name, "document_type": t.document_type, "version": t.version}
        for t in BUILTINS.values()
    ]
    rows.append(
        {"code": LABEL_TEMPLATE, "name": "Etiqueta de código de barras", "document_type": "inventory.label",
         "version": LABEL_VERSION}
    )  # fmt: skip
    return sorted(rows, key=lambda r: r["code"])
