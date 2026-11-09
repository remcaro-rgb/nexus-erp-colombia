"""Row validation rule engine for exógena format packs (F-ACC-EXO, P06 contribution).

P17 maps ledger balances to rows (dicts keyed by pack field names) and calls
:func:`validate_rows`; any ``error`` blocks file generation (T-ACC-EXO-03, P17).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from localization_co import nit
from localization_co.nit import DOCUMENT_TYPES_BY_CODE
from tax.exogena.loader import FormatPack, PackField, PackRule


@dataclass(frozen=True, slots=True)
class RowError:
    row: int
    field: str
    rule: str
    severity: str
    message: str

    def to_json(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "field": self.field,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(slots=True)
class ValidationReport:
    pack_code: str
    rows: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def blocking(self) -> list[RowError]:
        return [e for e in self.errors if e.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def to_json(self) -> dict[str, Any]:
        return {
            "pack_code": self.pack_code,
            "rows": self.rows,
            "ok": self.ok,
            "blocking_errors": len(self.blocking),
            "warnings": len(self.errors) - len(self.blocking),
            "errors": [e.to_json() for e in self.errors],
        }


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None  # floats are never accepted for money (C2)


def _type_error(f: PackField, value: Any) -> str | None:
    text = str(value)
    if len(text) > f.length:
        return f"longitud {len(text)} supera el máximo {f.length}"
    if f.type in ("amount", "decimal"):
        number = _decimal(value)
        if number is None:
            return "no es un valor decimal válido"
        if f.type == "amount" and number != number.quantize(Decimal(1).scaleb(-max(f.decimals, 0))):
            return f"admite como máximo {f.decimals} decimales"
    elif f.type == "integer" or f.type == "nit":
        if not text.isdigit():
            return "solo admite dígitos"
    elif f.type == "check_digit":
        if not (len(text) == 1 and text.isdigit()):
            return "el dígito de verificación es un solo dígito"
    elif f.type == "date":
        try:
            dt.date.fromisoformat(text)
        except ValueError:
            return "fecha inválida (AAAA-MM-DD)"
    elif f.type == "doc_type" and text not in DOCUMENT_TYPES_BY_CODE:
        return f"tipo de documento desconocido {text!r}"
    if f.enum and text not in f.enum:
        return f"valor {text!r} no permitido"
    return None


def _apply_rule(rule: PackRule, row: dict[str, Any], index: int, pack: FormatPack) -> list[RowError]:
    out: list[RowError] = []
    msg = rule.message_es

    def err(field_name: str, default: str) -> None:
        out.append(RowError(index, field_name, rule.id, rule.severity, msg or default))

    for name in rule.fields or ():
        value = row.get(name)
        if rule.kind == "required" and _empty(value):
            err(name, "campo obligatorio")
        elif rule.kind == "non_negative" and not _empty(value):
            number = _decimal(value)
            if number is not None and number < 0:
                err(name, "no admite valores negativos")
        elif rule.kind == "positive" and not _empty(value):
            number = _decimal(value)
            if number is not None and number <= 0:
                err(name, "debe ser mayor que cero")
        elif rule.kind == "max_length" and not _empty(value) and len(str(value)) > pack.field(name).length:
            err(name, "supera la longitud máxima")
        elif rule.kind == "enum" and not _empty(value) and str(value) not in pack.field(name).enum:
            err(name, "valor no permitido")
        elif rule.kind == "regex" and not _empty(value) and not re.fullmatch(rule.pattern, str(value)):
            err(name, "formato inválido")
        elif rule.kind == "municipality_code" and not _empty(value) and not re.fullmatch(r"[0-9]{5}", str(value)):
            err(name, "código DANE de municipio inválido (5 dígitos)")
    if rule.kind == "nit_check_digit" and len(rule.fields) == 2:
        number_field, dv_field = rule.fields
        number, dv = row.get(number_field), row.get(dv_field)
        doc_type = row.get("tipo_documento")
        applies = doc_type in (None, "", "NIT")
        if applies and not _empty(number):
            try:
                result = nit.validate(str(number), None if _empty(dv) else str(dv))
            except nit.NitInvalid:
                err(number_field, "NIT inválido")
            else:
                if _empty(dv):
                    err(dv_field, "falta el dígito de verificación")
                elif not result.valid:
                    err(dv_field, f"dígito de verificación incorrecto (esperado {result.check_digit})")
    return out


def validate_rows(pack: FormatPack, rows: list[dict[str, Any]]) -> ValidationReport:
    report = ValidationReport(pack.pack_code, rows=len(rows))
    known = {f.name for f in pack.fields}
    for i, row in enumerate(rows, start=1):
        for name in sorted(set(row) - known):
            report.errors.append(RowError(i, name, "SCHEMA", "error", "campo no definido en el formato"))
        for f in pack.fields:
            value = row.get(f.name)
            if _empty(value):
                if f.required:
                    report.errors.append(RowError(i, f.name, "SCHEMA", "error", "campo obligatorio"))
                continue
            problem = _type_error(f, value)
            if problem:
                report.errors.append(RowError(i, f.name, "SCHEMA", "error", problem))
        for rule in pack.rules:
            report.errors.extend(_apply_rule(rule, row, i, pack))
    report.errors.sort(key=lambda e: (e.row, e.field, e.rule))
    return report
