"""T-P02-01 — contract lint (the same rules as contracts/.spectral.yaml, executable without Node) + OpenAPI 3.1 meta-schema validation."""
from __future__ import annotations

import json
import os
import re

import jsonschema
import pytest

from contract_support import CONTRACTS, METHODS, ROOT, load_json, param_names, resolve

PERM_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(read|create|update|delete|approve|post|void|export|admin)$")
SEG_RE = re.compile(r"^(\{[a-z_]+\}|[a-z0-9][a-z0-9.-]*)(:[a-z][a-z0-9-]*)?$")
MUTATING = ("post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def oas_validator():
    schema = load_json("contracts", "tools", "schemas", "oas-3.1-2022-10-07.json")
    return jsonschema.Draft202012Validator(schema)


def test_T_P02_01_bundle_and_fragments_are_valid_openapi_3_1(oas_validator, bundle, root_doc, fragments):
    for name, doc in [("bundle", bundle), ("root", root_doc)] + sorted(fragments.items()):
        errs = [e.message for e in oas_validator.iter_errors(doc)]
        assert errs == [], (name, errs[:3])
        assert doc["openapi"] == "3.1.0"


def test_T_P02_01_yaml_and_json_bundles_are_identical(bundle):
    from contract_support import load_yaml
    assert load_yaml("contracts", "openapi", "openapi.yaml") == bundle


def test_T_P02_01_paths_follow_resource_and_action_conventions(bundle):
    bad = []
    for path in bundle["paths"]:
        if not (path.startswith("/api/v1/") or path.startswith("/api/portal/v1/")):
            bad.append(("prefix", path))
            continue
        rest = path.split("/v1/", 1)[1].split("/")
        for i, seg in enumerate(rest):
            if not SEG_RE.match(seg):
                bad.append(("segment", path, seg))
            if ":" in seg and i != len(rest) - 1:
                bad.append(("action-not-last", path))
    assert bad == []


def test_T_P02_01_operation_ids_unique_and_every_operation_documented(operations):
    ids = [o["operationId"] for (_m, _p, o) in operations]
    assert len(ids) == len(set(ids))
    for (m, p, o) in operations:
        assert o.get("summary") and o.get("tags"), (m, p)
        assert o.get("x-owner", "").startswith("P"), (m, p)
        assert "200" in o["responses"] or "201" in o["responses"] or "202" in o["responses"] or "204" in o["responses"], (m, p)
        for code in ("401", "403"):
            assert code in o["responses"], (m, p, code)


def test_T_P02_01_every_state_change_requires_idempotency_key(bundle, operations):
    missing = []
    for (m, p, o) in operations:
        if m in MUTATING:
            ps = param_names(bundle, o)
            if not (ps.get("Idempotency-Key") and ps["Idempotency-Key"]["required"]):
                missing.append(f"{m.upper()} {p}")
    assert missing == []


def test_T_P02_01_actions_are_explicit_post_endpoints(operations):
    bad = []
    for (m, p, o) in operations:
        if re.search(r":[a-z0-9-]+$", p):
            verb = p.rsplit(":", 1)[1]
            if m == "get":
                if verb not in ("export", "comparison") or o["x-permission"].rsplit(".", 1)[1] not in ("read", "export"):
                    bad.append(("get-action", p))
            elif m != "post":
                bad.append(("method", m, p))
    assert bad == []


def test_T_P02_01_updates_require_if_match_and_never_change_status(bundle, operations):
    bad = []
    for (m, p, o) in operations:
        if m in ("patch", "put"):
            ps = param_names(bundle, o)
            if not (ps.get("If-Match") and ps["If-Match"]["required"]):
                bad.append(("if-match", p))
            body = resolve(bundle, o["requestBody"]["content"]["application/json"]["schema"])
            if "status" in (body.get("properties") or {}):
                bad.append(("status-in-body", p))
            for k, v in (body.get("properties") or {}).items():
                if k.endswith("_status") and not v.get("readOnly"):
                    bad.append(("state-field-writable", p, k))
    assert bad == []


def test_T_P02_01_lists_are_cursor_paginated_with_limit_500(bundle, operations):
    lim = bundle["components"]["parameters"]["Limit"]["schema"]
    assert lim["maximum"] == 500
    bad = []
    for (m, p, o) in operations:
        if m == "get" and o["operationId"].endswith("_list"):
            ps = param_names(bundle, o)
            for need in ("cursor", "limit", "sort", "fields", "filter"):
                if need not in ps:
                    bad.append((p, need))
            schema = resolve(bundle, o["responses"]["200"]["content"]["application/json"]["schema"])
            assert schema["allOf"][0]["$ref"].endswith("/Page"), p
    assert bad == []
    filt = bundle["components"]["parameters"]["Filter"]
    ops = filt["schema"]["additionalProperties"]["propertyNames"]["enum"]
    assert ops == ["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "between", "isnull"]


def test_T_P02_01_long_running_operations_return_202_operation(bundle, operations):
    n = 0
    for (m, p, o) in operations:
        if "202" in o["responses"]:
            n += 1
            r = o["responses"]["202"]
            assert r["content"]["application/json"]["schema"]["$ref"].endswith("/Operation"), p
            assert "Location" in r["headers"], p
    assert n >= 20
    assert "/api/v1/operations/{id}" in bundle["paths"]


def test_T_P02_01_bulk_and_simulate_conventions(bundle, operations):
    bulk = [(m, p, o) for (m, p, o) in operations if p.endswith(":bulk")]
    sims = [(m, p, o) for (m, p, o) in operations if p.endswith(":simulate") or p.endswith(":preview")]
    assert len(bulk) >= 7 and len(sims) >= 25
    br = bundle["components"]["schemas"]["BulkRequest"]["properties"]
    assert br["items"]["maxItems"] == 1000 and "all_or_nothing" in br
    for (m, p, o) in bulk:
        assert o["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/BulkRequest")
        assert o["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/BulkResult")
    for (m, p, o) in sims:
        ref = o["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/SimulationResult"), p
    sr = bundle["components"]["schemas"]["SimulationResult"]
    assert sr["properties"]["persisted"] == {"const": False, "x-classification": "internal"}
    assert {"documents", "journal_entries", "stock_moves"} <= set(sr["required"])


def _walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}/{i}")


def test_T_P02_01_no_json_numbers_for_money_or_quantities(bundle):
    """D-001: decimals are strings; JSON `number` is forbidden anywhere in the contract (integers are allowed for counts)."""
    bad = [p for p, n in _walk(bundle) if n.get("type") == "number" or (isinstance(n.get("type"), list) and "number" in n["type"])]
    assert bad == []
    money = bundle["components"]["schemas"]["Money"]["properties"]["amount"]
    assert money["type"] == "string" and re.match(money["pattern"], "1234.5678") and not re.match(money["pattern"], "1e3")


def test_T_P02_01_errors_are_problem_json(bundle, operations):
    for name, r in bundle["components"]["responses"].items():
        assert list(r["content"]) == ["application/problem+json"], name
    prob = bundle["components"]["schemas"]["Problem"]
    assert {"type", "title", "status", "code", "trace_id"} <= set(prob["required"])
    for (m, p, o) in operations:
        for st, r in o["responses"].items():
            if st.startswith(("4", "5")):
                assert "$ref" in r and r["$ref"].startswith("#/components/responses/"), (m, p, st)


def test_T_P02_01_security_schemes_cover_users_services_and_delegation(bundle):
    ss = bundle["components"]["securitySchemes"]
    flows = ss["oauth2"]["flows"]
    assert {"authorizationCode", "clientCredentials"} <= set(flows)
    assert "token-exchange" in ss["tokenExchange"]["description"]
    assert {"oauth2": []} in bundle["security"] and {"tokenExchange": []} in bundle["security"]
    for s in bundle["servers"]:
        assert s["url"].startswith("http://127.0.0.1:"), "DEV-only: loopback servers only"


def test_T_P02_01_shared_components_present(root_doc):
    sc = root_doc["components"]["schemas"]
    for name in ("Money", "Quantity", "Rate", "Percent", "DateRange", "PartyRef", "DocumentRef", "AuditStamp", "Page", "Problem", "Operation",
                 "SimulationResult", "BulkRequest", "BulkResult", "DimensionSlots", "EventEnvelope", "ErrorCode"):
        assert name in sc, name
    assert set(sc["Page"]["required"]) == {"items", "next_cursor"}
    ps = root_doc["components"]["parameters"]
    assert ps["IdempotencyKey"]["name"] == "Idempotency-Key" and ps["IfMatch"]["name"] == "If-Match" and ps["CompanyHeader"]["name"] == "X-Company-Id"
    assert ps["Fields"]["name"] == "fields" and ps["Sort"]["name"] == "sort" and ps["Cursor"]["name"] == "cursor"


def test_T_P02_01_fragment_refs_resolve_and_bundle_is_their_union(bundle, root_doc, fragments):
    union = set(root_doc["paths"])
    for name, frag in fragments.items():
        for p in frag["paths"]:
            assert p not in union, p
            union.add(p)
        for _path, node in _walk(frag):
            ref = node.get("$ref") if isinstance(node, dict) else None
            if isinstance(ref, str):
                if ref.startswith("../root.yaml#/"):
                    cur = root_doc
                    for part in ref.split("#/", 1)[1].split("/"):
                        cur = cur[part]
                elif ref.startswith("#/"):
                    cur = frag
                    for part in ref[2:].split("/"):
                        cur = cur[part]
                else:
                    pytest.fail(f"{name}: unexpected ref {ref}")
    assert union == set(bundle["paths"])
    idx = {m["module"] for m in root_doc["x-modules"]}
    assert idx == set(fragments)


def test_T_P02_01_spectral_ruleset_present_and_mirrors_rules():
    import yaml
    with open(os.path.join(CONTRACTS, ".spectral.yaml"), encoding="utf-8") as fh:
        rs = yaml.safe_load(fh)
    rules = rs["rules"]
    for r in ("nexus-paths-versioned", "nexus-operation-permission", "nexus-mutating-idempotency-key", "nexus-no-json-number",
              "nexus-property-classification", "nexus-problem-json", "nexus-limit-max-500"):
        assert r in rules and rules[r].get("severity", "error") == "error", r
