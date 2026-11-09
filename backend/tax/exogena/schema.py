"""Exógena format-pack schema (F-ACC-EXO, P06 contribution).

A format pack describes one yearly información exógena format as data:
fields (name, type, length, required, enum, rule refs), the file layout
(XML/TXT, encoding, header) and validation rules. Real DIAN packs are an
external legal input (B-14, D-019) and are never invented: the repository ships
only the synthetic pack ``SYN-EXO-1001`` (``legal_status = NOT_DIAN_VALID``).

:func:`validate_pack` checks the structure and returns the list of problems;
:func:`require_valid_pack` raises ``exogena_pack_invalid`` with all of them.
"""

from __future__ import annotations

import re
from typing import Any

from tax.errors import ExogenaPackInvalid

FIELD_TYPES = frozenset({"string", "integer", "amount", "decimal", "date", "code", "nit", "check_digit", "doc_type"})
RULE_KINDS = frozenset(
    {"required", "max_length", "enum", "non_negative", "positive", "nit_check_digit", "regex", "municipality_code"}
)
SOURCES = frozenset({"SYNTHETIC", "OFFICIAL_IMPORTED"})
LEGAL_STATUSES = frozenset({"NOT_DIAN_VALID", "PENDING_VERIFICATION", "VERIFIED_BY_USER"})
FILE_FORMATS = frozenset({"XML", "TXT"})
SEVERITIES = frozenset({"error", "warning"})

REQUIRED_PACK_KEYS = ("pack_code", "format_code", "year", "version", "source", "legal_status", "fields", "file_spec")
REQUIRED_FIELD_KEYS = ("name", "type", "length", "required")
REQUIRED_RULE_KEYS = ("id", "kind", "severity")
REQUIRED_FILE_SPEC_KEYS = ("format", "encoding")
OPTIONAL_PACK_KEYS = ("label", "description", "rules", "concepts")

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SYNTHETIC_LABEL = "SINTÉTICO — no válido ante la DIAN"


def _problem(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _validate_fields(fields: Any, rule_ids: set[str]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    if not isinstance(fields, list) or not fields:
        return [_problem("fields", "must be a non-empty list")]
    seen: set[str] = set()
    for i, field in enumerate(fields):
        path = f"fields[{i}]"
        if not isinstance(field, dict):
            problems.append(_problem(path, "must be an object"))
            continue
        problems += [_problem(f"{path}.{k}", "missing required key") for k in REQUIRED_FIELD_KEYS if k not in field]
        name = field.get("name")
        if "name" in field:
            if not isinstance(name, str) or not _NAME.match(name):
                problems.append(_problem(f"{path}.name", "must match ^[a-z][a-z0-9_]{0,63}$"))
            elif name in seen:
                problems.append(_problem(f"{path}.name", f"duplicate field name {name!r}"))
            else:
                seen.add(name)
        if "type" in field and field["type"] not in FIELD_TYPES:
            problems.append(_problem(f"{path}.type", f"unknown type {field['type']!r}"))
        if "length" in field:
            length = field["length"]
            if isinstance(length, bool) or not isinstance(length, int) or not 0 < length <= 4000:
                problems.append(_problem(f"{path}.length", "must be an integer 1..4000"))
        if "required" in field and not isinstance(field["required"], bool):
            problems.append(_problem(f"{path}.required", "must be a boolean"))
        if "enum" in field and (not isinstance(field["enum"], list) or not field["enum"]):
            problems.append(_problem(f"{path}.enum", "must be a non-empty list"))
        for ref in field.get("rules", []) or []:
            if ref not in rule_ids:
                problems.append(_problem(f"{path}.rules", f"unknown rule reference {ref!r}"))
        unknown = set(field) - {"name", "type", "length", "required", "enum", "rules", "description", "decimals"}
        if unknown:
            problems.append(_problem(path, f"unknown keys {sorted(unknown)}"))
    return problems


def _validate_rules(rules: Any, field_names: set[str]) -> tuple[list[dict[str, str]], set[str]]:
    problems: list[dict[str, str]] = []
    ids: set[str] = set()
    if rules is None:
        return problems, ids
    if not isinstance(rules, list):
        return [_problem("rules", "must be a list")], ids
    for i, rule in enumerate(rules):
        path = f"rules[{i}]"
        if not isinstance(rule, dict):
            problems.append(_problem(path, "must be an object"))
            continue
        problems += [_problem(f"{path}.{k}", "missing required key") for k in REQUIRED_RULE_KEYS if k not in rule]
        rid = rule.get("id")
        if isinstance(rid, str) and rid:
            if rid in ids:
                problems.append(_problem(f"{path}.id", f"duplicate rule id {rid!r}"))
            ids.add(rid)
        if "kind" in rule and rule["kind"] not in RULE_KINDS:
            problems.append(_problem(f"{path}.kind", f"unknown rule kind {rule['kind']!r}"))
        if "severity" in rule and rule["severity"] not in SEVERITIES:
            problems.append(_problem(f"{path}.severity", "must be error|warning"))
        for f in rule.get("fields", []) or []:
            if f not in field_names:
                problems.append(_problem(f"{path}.fields", f"unknown field {f!r}"))
        if rule.get("kind") == "nit_check_digit" and len(rule.get("fields", []) or []) != 2:
            problems.append(_problem(f"{path}.fields", "nit_check_digit needs [number_field, dv_field]"))
        if rule.get("kind") == "regex":
            try:
                re.compile(str(rule.get("pattern", "")))
            except re.error:
                problems.append(_problem(f"{path}.pattern", "invalid regular expression"))
    return problems, ids


def validate_pack(pack: Any) -> list[dict[str, str]]:
    """Return every structural problem of ``pack`` (empty list = valid)."""
    if not isinstance(pack, dict):
        return [_problem("", "pack must be a JSON object")]
    problems = [_problem(k, "missing required key") for k in REQUIRED_PACK_KEYS if k not in pack]
    unknown = set(pack) - set(REQUIRED_PACK_KEYS) - set(OPTIONAL_PACK_KEYS)
    if unknown:
        problems.append(_problem("", f"unknown keys {sorted(unknown)}"))
    year = pack.get("year")
    if "year" in pack and (isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100):
        problems.append(_problem("year", "must be an integer 2000..2100"))
    for key in ("pack_code", "format_code", "version"):
        if key in pack and (not isinstance(pack[key], str) or not pack[key].strip()):
            problems.append(_problem(key, "must be a non-empty string"))
    source = pack.get("source")
    legal = pack.get("legal_status")
    if "source" in pack and source not in SOURCES:
        problems.append(_problem("source", f"must be one of {sorted(SOURCES)}"))
    if "legal_status" in pack and legal not in LEGAL_STATUSES:
        problems.append(_problem("legal_status", f"must be one of {sorted(LEGAL_STATUSES)}"))
    if source == "SYNTHETIC" and legal not in (None, "NOT_DIAN_VALID"):
        problems.append(_problem("legal_status", "a SYNTHETIC pack is always NOT_DIAN_VALID"))
    if source == "OFFICIAL_IMPORTED" and legal == "NOT_DIAN_VALID":
        problems.append(_problem("legal_status", "an imported official pack starts as PENDING_VERIFICATION"))
    fields = pack.get("fields")
    field_names = {f.get("name") for f in fields if isinstance(f, dict)} if isinstance(fields, list) else set()
    rule_problems, rule_ids = _validate_rules(pack.get("rules"), {n for n in field_names if isinstance(n, str)})
    problems += rule_problems
    if "fields" in pack:
        problems += _validate_fields(fields, rule_ids)
    spec = pack.get("file_spec")
    if "file_spec" in pack:
        if not isinstance(spec, dict):
            problems.append(_problem("file_spec", "must be an object"))
        else:
            problems += [
                _problem(f"file_spec.{k}", "missing required key") for k in REQUIRED_FILE_SPEC_KEYS if k not in spec
            ]
            if "format" in spec and spec["format"] not in FILE_FORMATS:
                problems.append(_problem("file_spec.format", "must be XML or TXT"))
    concepts = pack.get("concepts", [])
    if not isinstance(concepts, list) or any(not isinstance(c, dict) or "code" not in c for c in concepts):
        problems.append(_problem("concepts", "must be a list of objects with a code"))
    return problems


def require_valid_pack(pack: Any) -> dict[str, Any]:
    problems = validate_pack(pack)
    if problems:
        raise ExogenaPackInvalid(f"format pack invalid ({len(problems)} problems)", problems=problems)
    return pack  # type: ignore[no-any-return]
