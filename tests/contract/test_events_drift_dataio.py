"""T-P02-05 event fixtures validate (and broken ones do not); T-P02-06 the drift detector fails on an intentionally drifted fixture;
generated contracts are up to date with their sources; import/export specs; error registry; versioning; mock server."""
from __future__ import annotations

import copy
import glob
import json
import os
import re

import jsonschema
import pytest
import yaml

import build_contracts
import drift
from contract_support import CONTRACTS, ROOT, load_json, load_yaml

EVENT_FILES = sorted(p for p in glob.glob(os.path.join(CONTRACTS, "events", "*", "*.json")))
FORMAT_CHECKER = jsonschema.Draft202012Validator.FORMAT_CHECKER


def _example_for(schema_path):
    d, fn = os.path.split(schema_path)
    return os.path.join(d, "examples", fn)


def test_T_P02_05_every_event_has_schema_example_and_webhook(bundle):
    assert len(EVENT_FILES) >= 60
    types = set()
    for p in EVENT_FILES:
        s = json.load(open(p, encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(s)
        et = s["title"]
        types.add(et)
        assert re.match(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.v[0-9]+$", et)
        assert os.path.basename(p) == et + ".json" and os.path.basename(os.path.dirname(p)) == et.split(".")[0]
        assert s["properties"]["event_type"] == {"const": et}
        assert os.path.isfile(_example_for(p)), p
        assert et in bundle["webhooks"], et
    assert types == set(bundle["webhooks"])
    cat = load_yaml("contracts", "events", "catalog.yaml")
    assert {e["type"] for e in cat["events"]} == types


def test_T_P02_05_prompt_named_events_exist():
    named = ["ledger.entry.posted.v1", "ledger.entry.reversed.v1", "ledger.period.status_changed.v1", "inventory.move.done.v1",
             "inventory.valuation.changed.v1", "inventory.stock.below_min.v1", "inventory.count.approved.v1", "crm.lead.converted.v1",
             "crm.opportunity.won.v1", "sales.order.confirmed.v1", "sales.delivery.done.v1", "sales.invoice.posted.v1", "sales.credit_note.posted.v1",
             "sales.receipt.posted.v1", "purchasing.receipt.validated.v1", "purchasing.vendor_invoice.posted.v1", "purchasing.support_document.posted.v1",
             "purchasing.payment.posted.v1", "purchasing.einvoice.received.v1", "einvoice.document.status_changed.v1", "payroll.run.posted.v1",
             "iam.policy.changed.v1"]
    have = {json.load(open(p, encoding="utf-8"))["title"] for p in EVENT_FILES}
    assert sorted(set(named) - have) == []


@pytest.mark.parametrize("path", EVENT_FILES, ids=[os.path.basename(p)[:-5] for p in EVENT_FILES])
def test_T_P02_05_event_fixture_validates(path):
    schema = json.load(open(path, encoding="utf-8"))
    ex = json.load(open(_example_for(path), encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
    assert [e.message for e in v.iter_errors(ex)] == []
    # broken fixtures must be rejected
    for mutate in (
        lambda e: e.pop("event_id"),
        lambda e: e.__setitem__("event_type", "x.y.z.v9"),
        lambda e: e.__setitem__("event_id", "not-a-uuidv7"),
        lambda e: e["payload"].__setitem__("unexpected_field", 1),
        lambda e: e.__setitem__("schema_version", 1),
    ):
        bad = copy.deepcopy(ex)
        mutate(bad)
        assert list(v.iter_errors(bad)), path


def test_T_P02_05_money_in_events_rejects_json_numbers():
    p = os.path.join(CONTRACTS, "events", "treasury", "treasury.receipt.posted.v1.json")
    schema = json.load(open(p, encoding="utf-8"))
    ex = json.load(open(_example_for(p), encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema, format_checker=FORMAT_CHECKER)
    ex["payload"]["amount"]["amount"] = 1190000.5
    assert list(v.iter_errors(ex))


def test_T_P02_05_envelope_matches_shared_02():
    env = load_json("contracts", "events", "envelope.v1.json")
    assert set(env["required"]) == {"event_id", "event_type", "occurred_at", "tenant_id", "company_id", "actor", "correlation_id", "causation_id",
                                    "payload", "schema_version"}
    assert set(env["properties"]["actor"]["required"]) == {"type", "id"} and "on_behalf_of" in env["properties"]["actor"]["properties"]


# ---------------------------------------------------------------------------------------------------------------- drift (T-P02-06)
def test_T_P02_06_drift_detector_fails_on_intentionally_drifted_fixture(bundle):
    implemented = load_yaml("tests", "contract", "fixtures", "drifted-ledger-openapi.yaml")
    found = drift.compare(bundle, implemented, modules=None)
    kinds = {(k, d) for k, d in found}
    assert ("missing_parameter", "POST /api/v1/ledger/accounts header:Idempotency-Key") in kinds
    assert ("property_missing", "GET /api/v1/ledger/accounts/{id} 200 nature") in kinds
    assert ("property_type_changed", "GET /api/v1/ledger/accounts/{id} 200 postable: boolean → string") in kinds
    assert ("permission_mismatch", "GET /api/v1/ledger/accounts/{id} ledger.account.admin != ledger.account.read") in kinds
    assert ("undocumented_operation", "GET /api/v1/ledger/secret-dump") in kinds
    assert drift.main(["--implemented", os.path.join(ROOT, "tests", "contract", "fixtures", "drifted-ledger-openapi.yaml")]) == 1


def test_T_P02_06_drift_detector_is_clean_on_the_contract_itself_and_scoped_by_module(bundle):
    assert drift.compare(bundle, copy.deepcopy(bundle)) == []
    only_ledger = copy.deepcopy(bundle)
    only_ledger["paths"] = {p: v for p, v in bundle["paths"].items() if p.startswith("/api/v1/ledger/")}
    assert drift.compare(bundle, only_ledger, modules={"ledger"}) == []
    missing = drift.compare(bundle, only_ledger, modules={"ledger", "iam"})
    assert missing and all(k == "missing_operation" and "/api/v1/iam/" in d for k, d in missing)


def test_T_P02_06_drift_detects_decimal_to_number_and_added_required_field(bundle):
    impl = copy.deepcopy(bundle)
    impl["components"]["schemas"]["Account"]["properties"]["level"] = {"type": "number"}
    impl["components"]["schemas"]["AccountInput"]["required"] = impl["components"]["schemas"]["AccountInput"]["required"] + ["level"]
    found = drift.compare(bundle, impl, modules={"ledger"})
    assert ("property_type_changed", "GET /api/v1/ledger/accounts/{id} 200 level: integer → number") in found
    assert ("required_added", "POST /api/v1/ledger/accounts body.level") in found


def test_T_P02_06_generated_contracts_are_up_to_date_with_sources():
    outs = build_contracts.Builder().build()
    stale = []
    for rel, text in outs.items():
        p = os.path.join(CONTRACTS, rel)
        if not os.path.isfile(p) or open(p, encoding="utf-8").read() != text:
            stale.append(rel)
    assert stale == [], f"run python3 contracts/tools/build_contracts.py: {stale[:5]}"


# ---------------------------------------------------------------------------------------------------------------- dataio / errors / misc
def test_T_P02_dataio_specs_cover_at_least_30_entities_with_valid_columns(catalog):
    idx = load_yaml("contracts", "dataio", "index.yaml")["entities"]
    assert len(idx) >= 30
    codes = {c["code"] for c in catalog["codes"]}
    for ent in ["parties", "items", "price-lists", "chart-of-accounts", "opening-balances", "open-ar", "open-ap", "opening-stock", "employees", "contracts",
                "novelties", "fixed-assets", "bank-accounts", "users", "roles", "tax-profiles", "ica-tariffs", "budgets", "dimensions", "branches",
                "presentations", "locations", "boms", "formulas", "price-formulas", "payroll-concepts", "treasury-concepts", "issue-concepts",
                "internal-requisitions", "vendor-quotes", "gtins", "external-account-invitations", "exogena-format-packs"]:
        assert ent in idx, ent
    for ent in idx:
        spec = load_yaml("contracts", "dataio", ent + ".yaml")
        assert spec["entity"] == ent and spec["natural_key"] and spec["columns"], ent
        assert spec["permission"] in codes, (ent, spec["permission"])
        names = [c["name"] for c in spec["columns"]]
        assert len(names) == len(set(names)), ent
        for c in spec["columns"]:
            assert c["classification"] in {"public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret"}, (ent, c)
            assert c["classification"] != "secret", (ent, c["name"], "secrets are never imported/exported")
        if not spec.get("export_only"):
            assert spec["permission"].endswith(".create") or spec["permission"].endswith(".update"), ent


def test_T_P02_error_registry_is_stable_and_complete(errors_registry, operations):
    codes = [e["code"] for e in errors_registry["codes"]]
    assert len(codes) == len(set(codes))
    for e in errors_registry["codes"]:
        assert re.match(r"^[a-z][a-z0-9_]*$", e["code"])
        assert e["severity"] in ("error", "warning")
        assert (e["http"] == 200) == (e["severity"] == "warning"), e["code"]
        assert 400 <= e["http"] <= 599 or e["severity"] == "warning"
        assert e["title_es"] and e["title_en"]
    for c in ("stock_insufficient", "period_closed", "parameter_unknown", "fiscal_rejected", "policy_denied", "sod_violation", "idempotency_conflict",
              "version_conflict", "validation_failed"):
        assert c in codes
    used = set()
    for (_m, _p, o) in operations:
        for c in o["x-error-codes"]:
            assert c in codes, c
            used.add(c)
    assert len(used) >= 0.9 * len(codes)


def test_T_P02_versioning_policy_and_mock_server():
    txt = open(os.path.join(CONTRACTS, "VERSIONING.md"), encoding="utf-8").read()
    for needle in ("SemVer", "Deprecation", "Sunset", "CCR", "/api/v2/", ".v2"):
        assert needle in txt, needle
    compose = load_yaml("contracts", "mock", "compose.yaml")
    svc = compose["services"]["contract-mock"]
    assert re.match(r"^stoplight/prism:\d+\.\d+\.\d+$", svc["image"]), "pinned image, no latest"
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), "loopback only"
    assert any("openapi.yaml" in str(c) for c in svc["command"]) and any(str(v).endswith(":ro") for v in svc["volumes"])


def test_T_P02_interface_drafts_are_typed_protocols():
    import importlib.util
    import typing
    spec = importlib.util.spec_from_file_location("kernel_interfaces_draft", os.path.join(CONTRACTS, "interfaces", "kernel_interfaces_draft.py"))
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    names = ["PostingService", "StockService", "TaxService", "PolicyService", "AuditService", "WorkflowService", "SchedulerService", "FiscalGateway",
             "DocumentRenderer", "ReportQueryService", "NotificationService", "FileStore", "ParameterProvider",
             # stubs named in FEATURES-ADDENDUM
             "Dimensions", "Notifications", "Inventory", "Labels", "PayrollPayables", "FiscalDocuments", "PaymentStatus", "ExportFormat"]
    for n in names:
        cls = getattr(mod, n)
        assert getattr(cls, "_is_protocol", False), n
        assert cls.__doc__ and len(cls.__doc__) > 40, n
        methods = [k for k, v in vars(cls).items() if callable(v) and not k.startswith("_")]
        assert methods, n
        for k in methods:
            hints = typing.get_type_hints(getattr(cls, k))
            assert "return" in hints, (n, k)
