"""T-P02-03 every operation has a permission code in the catalog; T-P02-04 every schema property has a field classification;
plus the default role templates of the P02 prompt."""
from __future__ import annotations

import fnmatch
import re

from contract_support import load_yaml

PERM_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.(read|create|update|delete|approve|post|void|export|admin)$")
CLASSES = {"public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret"}


def test_T_P02_03_every_operation_has_a_catalogued_permission(operations, bundle, catalog):
    codes = {c["code"]: c for c in catalog["codes"]}
    for (m, p, o) in operations:
        perm = o.get("x-permission")
        assert perm and PERM_RE.match(perm), (m, p, perm)
        assert perm in codes, (m, p, perm)
        assert o["operationId"] in codes[perm]["operations"], (m, p)
    for ev, w in bundle["webhooks"].items():
        assert w["post"]["x-permission"] in codes, ev
    assert catalog["grammar"]["actions"] == ["read", "create", "update", "delete", "approve", "post", "void", "export", "admin"]


def test_T_P02_03_catalog_codes_are_well_formed_and_unique(catalog, operations):
    seen = set()
    op_ids = {o["operationId"] for (_m, _p, o) in operations}
    for c in catalog["codes"]:
        assert PERM_RE.match(c["code"]), c["code"]
        assert c["code"] not in seen
        seen.add(c["code"])
        assert c["code"].split(".")[0] == c["module"]
        if c.get("source") == "P02":
            assert c["operations"], c["code"]
            for oid in c["operations"]:
                assert oid in op_ids or oid.startswith("event_"), (c["code"], oid)


def test_T_P02_03_permissions_follow_action_semantics(operations):
    for (m, p, o) in operations:
        act = o["x-permission"].rsplit(".", 1)[1]
        if m == "delete":
            assert act == "delete", p
        if p.endswith(":post") or p.endswith(":checkout"):
            assert act == "post", p
        if p.endswith((":approve", ":reject")):
            assert act == "approve", p
        if p.endswith((":reverse", ":void")):
            assert act == "void", p
        if p.endswith((":simulate", ":preview")):
            assert act == "read", p
        if o["operationId"].endswith("_list") or (m == "get" and act not in ("read", "export")):
            assert act in ("read", "export"), p


def test_T_P02_03_default_role_templates(catalog):
    roles = {r["name_es"]: r for r in catalog["roles"]}
    expected = ["Administrador", "Contador", "Auxiliar contable", "Vendedor", "Cajero POS", "Jefe de bodega", "Comprador", "Analista de nómina",
                "Empleado (autoservicio)", "Gerente", "Auditor (solo lectura)", "Agente IA (solo delegado)"]
    assert sorted(roles) == sorted(expected)
    codes = [c["code"] for c in catalog["codes"]]
    for r in catalog["roles"]:
        assert r["max_classification"] in CLASSES
        for pat in r.get("grants", []) + r.get("denies", []) + r.get("delegation_ceiling", []):
            assert re.match(r"^[a-z_*]+\.[a-z_*]+\.(\*|read|create|update|delete|approve|post|void|export|admin)$", pat), (r["key"], pat)
            assert fnmatch.filter(codes, pat), (r["key"], pat, "pattern matches no code")

    def effective(role):
        g = {c for c in codes if any(fnmatch.fnmatch(c, p) for p in role.get("grants", []))}
        return {c for c in g if not any(fnmatch.fnmatch(c, p) for p in role.get("denies", []))}

    auditor = effective(roles["Auditor (solo lectura)"])
    assert auditor and all(c.endswith(".read") for c in auditor)
    assert "workflow.audit_event.read" in auditor
    ia = roles["Agente IA (solo delegado)"]
    assert ia["delegated_only"] is True and ia["grants"] == [] and effective(ia) == set()
    cashier = effective(roles["Cajero POS"])
    assert "pos.ticket.post" in cashier and not any(c.startswith("ledger.") for c in cashier)
    emp = effective(roles["Empleado (autoservicio)"])
    assert emp and all(c.startswith(("portal.employee_", "hr.self_service.", "timekeeping.clock_event.")) for c in emp)
    seller = effective(roles["Vendedor"])
    assert not any(c.endswith((".post", ".approve", ".admin")) for c in seller)
    admin = effective(roles["Administrador"])
    assert not any(c.endswith(".post") for c in admin)


def _props(schema_name, schema, out):
    if not isinstance(schema, dict):
        return
    for k, v in (schema.get("properties") or {}).items():
        out.append((schema_name, k, v))
        _props(f"{schema_name}.{k}", v, out)
        if isinstance(v, dict) and isinstance(v.get("items"), dict):
            _props(f"{schema_name}.{k}[]", v["items"], out)
    for key in ("allOf", "oneOf", "anyOf"):
        for i, part in enumerate(schema.get(key, []) or []):
            _props(f"{schema_name}/{key}{i}", part, out)
    if isinstance(schema.get("items"), dict):
        _props(schema_name + "[]", schema["items"], out)
    if isinstance(schema.get("additionalProperties"), dict):
        _props(schema_name + "{}", schema["additionalProperties"], out)


def test_T_P02_04_every_schema_property_has_a_classification(bundle):
    out = []
    for name, s in bundle["components"]["schemas"].items():
        _props(name, s, out)
    missing = [(s, k) for (s, k, v) in out if not (isinstance(v, dict) and v.get("x-classification") in CLASSES)]
    assert len(out) > 5000
    assert missing == []


def test_T_P02_04_sensitive_fields_are_classified_conservatively(bundle):
    sc = bundle["components"]["schemas"]
    assert sc["Employee"]["properties"]["health_entity"]["x-classification"] == "sensitive-pii"
    assert sc["Employee"]["properties"]["document"]["x-classification"] == "restricted-pii"
    assert sc["Party"]["properties"]["email"]["x-classification"] == "restricted-pii"
    for name, s in sc.items():
        for k, v in (s.get("properties") or {}).items():
            if k.endswith(("_ref",)) and ("secret" in k or "credentials" in k or "pin" in k or "technical_key" in k):
                assert v["x-classification"] == "secret" and v.get("writeOnly") is True, (name, k)
            if k in ("email", "phone") and not name.startswith(("Branch", "Company")):  # organisation contact data is internal
                assert v["x-classification"] in ("restricted-pii", "sensitive-pii"), (name, k)
    # nothing secret is ever readable
    for name, s in sc.items():
        for k, v in (s.get("properties") or {}).items():
            if v.get("x-classification") == "secret":
                assert v.get("writeOnly") is True, (name, k)


def test_T_P02_04_field_classification_list_in_catalog(catalog):
    assert catalog["grammar"]["field_classifications"] == ["public", "internal", "confidential", "restricted-pii", "sensitive-pii", "secret"]


def test_T_P02_03_catalog_keeps_codes_appended_by_later_tasks(tmp_path, monkeypatch):
    """Union-merge contract: codes appended by a later task (source != P02) survive regeneration."""
    import build_contracts as bc
    real = load_yaml("contracts", "permissions", "catalog.yaml")
    extra = {"code": "inventory.zz_future.read", "module": "inventory", "owner": "P09", "description": "appended by P09", "operations": [], "source": "P09"}
    real["codes"].append(extra)
    fake = tmp_path / "contracts"
    (fake / "permissions").mkdir(parents=True)
    import yaml
    (fake / "permissions" / "catalog.yaml").write_text(yaml.safe_dump(real, sort_keys=False, allow_unicode=True), encoding="utf-8")
    b = bc.Builder()
    monkeypatch.setattr(bc, "CONTRACTS", str(fake))
    b.build()
    out = yaml.safe_load(b.outputs["permissions/catalog.yaml"])
    assert any(c["code"] == "inventory.zz_future.read" and c["source"] == "P09" for c in out["codes"])


def test_T_P02_04_secrets_are_never_in_read_models(bundle):
    """Secret references and uploads are write-only: they appear only in request bodies (…Input/…Patch), never in a response schema."""
    sc = bundle["components"]["schemas"]
    request_bodies = set()
    for path, item in bundle["paths"].items():
        for m, o in item.items():
            rb = o.get("requestBody", {}).get("content", {})
            for ct in rb.values():
                ref = ct.get("schema", {}).get("$ref", "")
                if ref:
                    request_bodies.add(ref.rsplit("/", 1)[1])
    found = []
    for name, s in sc.items():
        for k, v in (s.get("properties") or {}).items():
            if v.get("x-classification") == "secret" or v.get("writeOnly"):
                found.append(name)
                assert name in request_bodies, (name, k)
    assert found, "expected write-only secret references in request bodies (providers, webhooks, numbering ranges, settings)"
