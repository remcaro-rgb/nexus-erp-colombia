"""T-X-QA-06 — the frozen OpenAPI contains every endpoint, event and stable error code listed in FEATURES-ADDENDUM
(contract diff test against docs/spec/features/feature-registry.json). Gate G0, owner P02.

The positive test runs the diff engine (contracts/tools/registry_diff.py) over the real registry, addendum and bundled contract.
The other T-X-QA-06 tests prove the diff is not vacuous: each removes one listed element from an in-memory copy of the contract and
expects the diff to report exactly that gap.
"""
from __future__ import annotations

import copy
import re

import pytest

import registry_diff as rd
from contract_support import ROOT


@pytest.fixture(scope="module")
def baseline():
    problems, stats = rd.run(ROOT)
    return problems, stats


def run_with(mutator):
    orig = rd.Contract.__init__

    def init(self, root):
        orig(self, root)
        mutator(self)

    rd.Contract.__init__ = init
    try:
        problems, _ = rd.run(ROOT)
    finally:
        rd.Contract.__init__ = orig
    return problems


def test_T_X_QA_06_frozen_openapi_contains_every_registry_endpoint_event_and_error(baseline, feature_registry):
    problems, stats = baseline
    assert problems == [], "\n".join(problems)
    # the diff really walked the whole registry
    assert stats["features"] == len(feature_registry["features"]) == feature_registry["counts"]["features"]
    assert stats["api_lines"] == sum(len(f["api"]) for f in feature_registry["features"])
    assert stats["error_codes"] == sum(len(f["errors"]) for f in feature_registry["features"])
    assert stats["tokens"] >= 150
    assert stats["events"] >= 20


def test_T_X_QA_06_registry_and_addendum_twins_agree():
    with open(f"{ROOT}/docs/spec/FEATURES-ADDENDUM.md", encoding="utf-8") as fh:
        sections = rd.addendum_sections(fh.read())
    reg = rd.json.load(open(f"{ROOT}/docs/spec/features/feature-registry.json", encoding="utf-8"))
    with_api = [f["id"] for f in reg["features"] if f["api"]]
    assert all(sections[fid]["api"] for fid in with_api)
    assert sum(len(s["errors"]) for s in sections.values()) == sum(len(f["errors"]) for f in reg["features"])


def test_T_X_QA_06_every_registry_absolute_path_is_in_the_bundle(feature_registry, bundle):
    """Independent of the token engine: every literal absolute path (alternations expanded) exists, modulo param names."""
    def norm(p):
        return re.sub(r"\{[^}]+\}", "{}", p)
    paths = {norm(p) for p in bundle["paths"]}
    missing = []
    for f in feature_registry["features"]:
        for line in f["api"]:
            for tok in re.findall(r"`(/api/[^`?]+)", line):
                for alt in rd.expand_alternations(tok):
                    if norm(alt) not in paths:
                        missing.append((f["id"], alt))
    assert missing == []


def test_T_X_QA_06_every_registry_error_code_is_frozen(feature_registry, errors_registry, bundle):
    registered = {e["code"] for e in errors_registry["codes"]}
    enum = set(bundle["components"]["schemas"]["ErrorCode"]["enum"])
    assert registered == enum
    for f in feature_registry["features"]:
        for e in f["errors"]:
            code = rd.error_code_of(e)
            if code.endswith("*"):
                assert any(c.startswith(code[:-1]) for c in registered), (f["id"], code)
            else:
                assert code in registered and code in enum, (f["id"], code)


def _drop_paths(pred):
    def m(c):
        c.ops[:] = [o for o in c.ops if not pred(o[0], o[1])]
    return m


@pytest.mark.parametrize("desc,mutator,expect", [
    ("dimension tree endpoint removed", _drop_paths(lambda m, p: p.endswith("/cost-centers/tree")), "F-ACC-DIM"),
    ("branch archive action removed", _drop_paths(lambda m, p: p.endswith("/iam/branches/{id}:archive")), "F-ACC-BRANCH"),
    ("treasury receipt :reverse removed", _drop_paths(lambda m, p: p == "/api/v1/treasury/receipts/{id}:reverse"), "F-TRE-RC"),
    ("CE payment batch execute removed", _drop_paths(lambda m, p: p.endswith("/payment-batches/{id}:execute")), "F-TRE-CE"),
    ("BOM PATCH removed (CRUD)", _drop_paths(lambda m, p: m == "patch" and p == "/api/v1/inventory/boms/{id}"), "F-INV-BOM"),
    ("portal supplier payments removed", _drop_paths(lambda m, p: p.startswith("/api/portal/v1/supplier/payments")), "F-PORTAL-SUP"),
    ("exógena run file removed", _drop_paths(lambda m, p: p == "/api/v1/reporting/exogena/runs/{id}/file"), "F-ACC-EXO"),
    ("one standard report removed", _drop_paths(lambda m, p: p == "/api/v1/reporting/standard/flujo-efectivo"), "F-ACC-REPORTS"),
    ("ai tools listing removed", _drop_paths(lambda m, p: p == "/api/v1/ai/tools"), "F-X-AI-MCP"),
])
def test_T_X_QA_06_diff_detects_missing_endpoint(desc, mutator, expect):
    problems = run_with(mutator)
    assert any(p.startswith(expect + ":") for p in problems), f"{desc}: not detected"


def test_T_X_QA_06_diff_detects_missing_query_parameter():
    def m(c):
        for (meth, p, o) in c.ops:
            if p == "/api/v1/inventory/boms/{id}/explode":
                o["parameters"] = [x for x in o["parameters"] if x.get("name") != "qty"]
    problems = run_with(m)
    assert any("F-INV-BOM" in p and "`qty`" in p for p in problems)


def test_T_X_QA_06_diff_detects_untagged_feature_operations():
    def m(c):
        for (_m, p, o) in c.ops:
            if p.startswith("/api/v1/inventory/transformations"):
                o["x-features"] = []
    problems = run_with(m)
    assert any(p.startswith("F-INV-XFORM:") for p in problems)


def test_T_X_QA_06_diff_detects_unregistered_or_undeclared_error_code():
    problems = run_with(lambda c: c.errors.pop("three_way_mismatch"))
    assert any("three_way_mismatch not in contracts/errors.yaml" in p for p in problems)

    def undeclare(c):
        for (_m, _p, o) in c.ops:
            o["x-error-codes"] = [e for e in o.get("x-error-codes", []) if e != "dimension_cycle"]
    problems = run_with(undeclare)
    assert any("dimension_cycle not declared" in p for p in problems)


def test_T_X_QA_06_diff_detects_missing_event():
    def m(c):
        for k in [k for k, v in c.webhooks.items() if "F-INV-ISSUE" in (v["post"].get("x-features") or [])]:
            c.webhooks.pop(k)
    problems = run_with(m)
    assert any(p.startswith("F-INV-ISSUE:") and "event" in p for p in problems)


def test_T_X_QA_06_diff_detects_missing_idempotency_key_on_treasury():
    def m(c):
        for (meth, p, o) in c.ops:
            if p == "/api/v1/treasury/disbursements/{id}:post":
                o["parameters"] = [x for x in o["parameters"] if not x.get("$ref", "").endswith("/IdempotencyKey")]
    problems = run_with(m)
    assert any(p.startswith("F-TRE-CE:") and "Idempotency-Key" in p for p in problems)


def test_T_X_QA_06_line_parser_reads_groups_methods_and_queries():
    toks = rd.parse_line("CRUD `/api/v1/inventory/boms` (+`:activate`, `:obsolete`) · GET `/api/v1/inventory/boms/{id}/explode?qty=&date=`")
    assert [t["token"] for t in toks] == ["/api/v1/inventory/boms", ":activate", ":obsolete", "/api/v1/inventory/boms/{id}/explode?qty=&date="]
    assert toks[0]["methods"] == ["CRUD"] and toks[3]["methods"] == ["GET"]
    assert toks[1]["anchor"] == 0 and toks[2]["anchor"] == 0 and toks[3]["anchor"] is None
    assert rd.expand_alternations("/a/{b|c}/d") == ["/a/b/d", "/a/c/d"]
    assert rd.split_query("/x?qty=&date=") == ("/x", ["qty", "date"])
    assert rd.error_code_of("treasury_insufficient_funds_warning (CE; blocking only …)") == "treasury_insufficient_funds_warning"
    assert rd.error_code_of("formula_*") == "formula_*"
    assert rd.error_code_of("gs1_prefix_missing") == "gs1_prefix_missing"
