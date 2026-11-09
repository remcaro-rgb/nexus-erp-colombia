"""Row policy language → Django ``Q`` and in-memory evaluation.

``{"resource": "sales.invoice", "effect": "allow", "actions": ["read"],
"where": {"all": [{"attr": "company_id", "op": "in", "value": "$principal.company_ids"},
{"attr": "salesperson_id", "op": "eq", "value": "$principal.employee_id"}]}}``.
Deny overrides allow; no applicable allow (when allow rules exist) → deny.
Unknown operators / malformed rules raise ``ValidationFailed`` (never silently allow).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db.models import Q

from kernel.errors import ValidationFailed

OPS = ("eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "isnull")
_LOOKUP = {"eq": "exact", "in": "in", "lt": "lt", "lte": "lte", "gt": "gt", "gte": "gte", "isnull": "isnull"}


def validate(rule: Mapping[str, Any]) -> None:
    if rule.get("effect") not in ("allow", "deny"):
        raise ValidationFailed("row policy effect must be allow or deny")
    if not isinstance(rule.get("actions"), list) or not rule["actions"]:
        raise ValidationFailed("row policy needs actions")
    _validate_where(rule.get("where") or {"all": []})


def _validate_where(node: Mapping[str, Any]) -> None:
    if "all" in node or "any" in node:
        children = node.get("all", node.get("any"))
        if not isinstance(children, list):
            raise ValidationFailed("where.all/any must be a list")
        for child in children:
            _validate_where(child)
        return
    if node.get("op") not in OPS or not isinstance(node.get("attr"), str) or not node["attr"].isidentifier():
        raise ValidationFailed("invalid row policy predicate")


def resolve(value: Any, principal: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$principal."):
        key = value.removeprefix("$principal.")
        if key not in principal:
            raise ValidationFailed(f"unknown principal attribute {key}")
        return principal[key]
    return value


def to_q(node: Mapping[str, Any], principal: Mapping[str, Any], fields: set[str]) -> Q:
    if "all" in node:
        q = Q()
        for child in node["all"]:
            q &= to_q(child, principal, fields)
        return q
    if "any" in node:
        children = node["any"]
        if not children:
            return Q(pk__in=[])
        q = Q(pk__in=[])
        for child in children:
            q |= to_q(child, principal, fields)
        return q
    attr, op = node["attr"], node["op"]
    if attr not in fields:
        return Q(pk__in=[])  # rule over an absent attribute never matches (fail closed)
    value = resolve(node.get("value"), principal)
    if op == "ne":
        return ~Q(**{attr: value})
    if op == "not_in":
        return ~Q(**{f"{attr}__in": list(value or [])})
    if op == "in":
        value = list(value or [])
    return Q(**{f"{attr}__{_LOOKUP[op]}": value})


def evaluate(node: Mapping[str, Any], principal: Mapping[str, Any], obj: Mapping[str, Any]) -> bool:
    if "all" in node:
        return all(evaluate(c, principal, obj) for c in node["all"])
    if "any" in node:
        return any(evaluate(c, principal, obj) for c in node["any"])
    attr, op = node["attr"], node["op"]
    if attr not in obj:
        return False
    actual, value = obj[attr], resolve(node.get("value"), principal)
    norm = str if actual is not None and not isinstance(actual, bool | int) else (lambda v: v)
    if op == "isnull":
        return (actual is None) is bool(value)
    if op in ("in", "not_in"):
        inside = norm(actual) in {norm(v) for v in (value or [])}
        return inside if op == "in" else not inside
    if op == "eq":
        return bool(norm(actual) == norm(value))
    if op == "ne":
        return bool(norm(actual) != norm(value))
    if actual is None or value is None:
        return False
    return bool({"lt": actual < value, "lte": actual <= value, "gt": actual > value, "gte": actual >= value}[op])
