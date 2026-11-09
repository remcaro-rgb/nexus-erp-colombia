#!/usr/bin/env python3
"""Drift detector: implemented OpenAPI (e.g. generated from the Django URL conf by drf-spectacular) vs the frozen contract.

From W1 on, module owners run it in CI (P03 wires it) against the schema their backend generates:
    python3 contracts/tools/drift.py --implemented reports/schema/backend.yaml [--modules ledger,iam] [--contract contracts/openapi/openapi.json]
Exit 0 = no drift; 1 = drift (each finding printed as `DRIFT <kind> <detail>`).

Drift kinds:
  missing_operation        a contract operation of a checked module is not implemented
  undocumented_operation   an implemented operation is not in the contract (no private or fake endpoints)
  missing_parameter        a required contract parameter (e.g. Idempotency-Key, If-Match, path id) is absent or optional
  permission_mismatch      implemented `x-permission` differs from the contract
  response_status_missing  a contract 2xx status is not produced
  property_missing         a contract response property is absent from the implemented 2xx JSON schema
  property_type_changed    a property changed JSON type (e.g. decimal string → number)
  required_added           the implementation requires a request property the contract does not require (breaking for clients)
Only modules listed with --modules are checked for `missing_operation` (backends land module by module); undocumented operations are always drift.
"""
from __future__ import annotations

import json
import os
import re
import sys

METHODS = ("get", "post", "put", "patch", "delete")


def load(path):
    with open(path, encoding="utf-8") as fh:
        if path.endswith(".json"):
            return json.load(fh)
        import yaml
        return yaml.safe_load(fh)


def norm(path):
    return re.sub(r"\{[^}]+\}", "{}", path)


def module_of(path):
    m = re.match(r"^/api/(?:portal/)?v1/([a-z0-9-]+)", path)
    if path.startswith("/api/portal/"):
        return "portal"
    return m.group(1) if m else None


def deref(doc, node, depth=0):
    while isinstance(node, dict) and "$ref" in node and node["$ref"].startswith("#/") and depth < 50:
        cur = doc
        for part in node["$ref"][2:].split("/"):
            cur = cur[part]
        node, depth = cur, depth + 1
    return node


def flat_props(doc, schema, depth=0):
    """name -> json type ('string'|'object'|...) of top-level properties, following allOf and $ref."""
    schema = deref(doc, schema)
    out = {}
    if not isinstance(schema, dict) or depth > 8:
        return out
    for part in schema.get("allOf", []):
        out.update(flat_props(doc, part, depth + 1))
    for k, v in (schema.get("properties") or {}).items():
        v2 = deref(doc, v)
        t = v2.get("type") if isinstance(v2, dict) else None
        if t is None and isinstance(v2, dict) and "properties" in v2:
            t = "object"
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        out[k] = t
    return out


def required_props(doc, schema):
    schema = deref(doc, schema)
    req = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    for part in (schema.get("allOf", []) if isinstance(schema, dict) else []):
        req |= required_props(doc, part)
    return req


def ops(doc):
    out = {}
    for p, item in (doc.get("paths") or {}).items():
        for m in METHODS:
            if m in item:
                out[(m, norm(p))] = (p, item[m], item.get("parameters", []))
    return out


def params(doc, op, item_params):
    out = {}
    for p in list(item_params) + list(op.get("parameters", [])):
        p = deref(doc, p)
        out[(p["name"], p["in"])] = bool(p.get("required"))
    return out


def json_schema_of(resp_or_body, doc):
    r = deref(doc, resp_or_body)
    c = (r or {}).get("content") or {}
    for ct in ("application/json", "application/problem+json"):
        if ct in c:
            return c[ct].get("schema")
    return None


def compare(contract, implemented, modules=None):
    findings = []
    c_ops, i_ops = ops(contract), ops(implemented)
    for key, (path, op, ip) in sorted(c_ops.items()):
        if modules is not None and module_of(path) not in modules:
            continue
        if key not in i_ops:
            findings.append(("missing_operation", f"{key[0].upper()} {path}"))
            continue
        ipath, iop, iip = i_ops[key]
        cp, impl_p = params(contract, op, ip), params(implemented, iop, iip)
        for (name, loc), req in cp.items():
            if req and not impl_p.get((name, loc)):
                findings.append(("missing_parameter", f"{key[0].upper()} {path} {loc}:{name}"))
        if iop.get("x-permission") and iop.get("x-permission") != op.get("x-permission"):
            findings.append(("permission_mismatch", f"{key[0].upper()} {path} {iop.get('x-permission')} != {op.get('x-permission')}"))
        for status, resp in op.get("responses", {}).items():
            if not status.startswith("2"):
                continue
            if status not in iop.get("responses", {}):
                findings.append(("response_status_missing", f"{key[0].upper()} {path} {status}"))
                continue
            cs = json_schema_of(resp, contract)
            isch = json_schema_of(iop["responses"][status], implemented)
            if cs is None or isch is None:
                continue
            cprops, iprops = flat_props(contract, cs), flat_props(implemented, isch)
            for name, t in cprops.items():
                if name not in iprops:
                    findings.append(("property_missing", f"{key[0].upper()} {path} {status} {name}"))
                elif t and iprops[name] and t != iprops[name]:
                    findings.append(("property_type_changed", f"{key[0].upper()} {path} {status} {name}: {t} → {iprops[name]}"))
        cb, ib = op.get("requestBody"), iop.get("requestBody")
        if cb and ib:
            cs, isch = json_schema_of(cb, contract), json_schema_of(ib, implemented)
            if cs is not None and isch is not None:
                extra = required_props(implemented, isch) - required_props(contract, cs)
                for name in sorted(extra):
                    findings.append(("required_added", f"{key[0].upper()} {path} body.{name}"))
    for key, (path, _op, _ip) in sorted(i_ops.items()):
        if key not in c_ops:
            findings.append(("undocumented_operation", f"{key[0].upper()} {path}"))
    return findings


def main(argv):
    import argparse
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--contract", default=os.path.join(here, "..", "openapi", "openapi.json"))
    ap.add_argument("--implemented", required=True)
    ap.add_argument("--modules", default=None, help="comma-separated modules whose contract operations must all be implemented")
    a = ap.parse_args(argv)
    mods = set(a.modules.split(",")) if a.modules else None
    f = compare(load(a.contract), load(a.implemented), mods)
    for kind, detail in f:
        print("DRIFT", kind, detail)
    print("DRIFT-NONE" if not f else f"DRIFT-FOUND {len(f)}")
    return 0 if not f else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
