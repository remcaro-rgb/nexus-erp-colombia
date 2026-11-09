"""Applicability matrix: seller regime × buyer regime × concept × municipality.

A tax code carries an ordered list of rules (data, ``tax/fixtures/tax_codes_default.yaml``)::

    {"when": {"buyer.vat_withholding_agent": true, "seller.regime": ["RESPONSABLE_IVA"]}, "apply": true}
    {"apply": false}

The first rule whose ``when`` matches decides; no rules → the code applies.
Keys: ``seller.<attr>``, ``buyer.<attr>``, ``concept``, ``municipality``,
``activity``. A list value means "one of"; ``"*"`` matches anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tax.engine.types import DocumentInput, DocumentLine


@dataclass(frozen=True, slots=True)
class MatrixDecision:
    applies: bool
    rule_index: int | None
    reason: str


def _value(key: str, doc: DocumentInput, line: DocumentLine) -> Any:
    if key.startswith("seller."):
        return doc.seller.attr(key.split(".", 1)[1])
    if key.startswith("buyer."):
        return doc.buyer.attr(key.split(".", 1)[1])
    if key == "concept":
        return line.concept
    if key == "municipality":
        return doc.municipality
    if key == "activity":
        return line.activity_code
    raise KeyError(f"unknown applicability key {key!r}")


def _matches(expected: Any, actual: Any) -> bool:
    if expected == "*":
        return True
    if isinstance(expected, list):
        return actual in expected
    return bool(actual == expected)


def decide(rules: tuple[dict[str, Any], ...], doc: DocumentInput, line: DocumentLine) -> MatrixDecision:
    if not rules:
        return MatrixDecision(True, None, "sin reglas de aplicabilidad: aplica")
    for i, rule in enumerate(rules):
        when = rule.get("when", {}) or {}
        if all(_matches(exp, _value(key, doc, line)) for key, exp in when.items()):
            applies = bool(rule.get("apply", True))
            reason = rule.get("reason") or (f"regla {i + 1}: " + ", ".join(f"{k}={v}" for k, v in when.items()))
            return MatrixDecision(applies, i, str(reason) if when else f"regla {i + 1}: por defecto")
    return MatrixDecision(False, None, "ninguna regla coincide: no aplica")
