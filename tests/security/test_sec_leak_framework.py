"""P22a: the invariant-12 leak framework proves itself before it is trusted (mutation tests).

A correct reference API must pass every generated case; each mutant that drops one control
(authentication, function permission, tenant/company/owner/warehouse row filter, field mask,
error-body echo) must be caught by at least one case of the expected kind.
"""

from __future__ import annotations

import pytest
from qa_harness import openapi
from qa_harness.datasets import seed_resource
from qa_harness.leak import CaseKind, LeakSuite, Status, generate_cases, summarize, write_matrix_csv
from qa_harness.openapi import Channel
from qa_harness.oracle import Dataset
from qa_harness.principals import Kind, matrix
from reference_api import SUPPORTED, ReferenceApi


def _fields(resource: str, n: int) -> dict[str, str]:
    if resource == "sales.party":
        return {"code": f"C-{n:04d}", "legal_name": f"Cliente Andino SINTÉTICO {n} SAS", "email": f"cliente{n}@demo.invalid"}
    if resource == "hr.employee":
        return {"code": f"E-{n:04d}", "full_name": f"Empleado SINTÉTICO {n}", "health_entity": f"EPS SINTÉTICA {n}"}
    return {"code": f"SYN-{n:04d}"}


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    m = matrix()
    ds = Dataset()
    seed_resource(ds, m, "sales.party", owners=(Kind.SALESPERSON, Kind.ADMIN), fields=_fields)
    seed_resource(ds, m, "hr.employee", owners=(Kind.PAYROLL,), fields=_fields)
    seed_resource(ds, m, "inventory.move", owners=(Kind.WAREHOUSE,), warehouses=(1, 2), fields=_fields)
    seed_resource(ds, m, "inventory.issue", owners=(Kind.WAREHOUSE,), warehouses=(1, 2), fields=_fields)
    seed_resource(ds, m, "iam.user", owners=(Kind.ADMIN,), company_scoped=False, fields=_fields)  # tenant-level
    return ds


@pytest.fixture(scope="module")
def cases(dataset):
    ops = [op for op in openapi.operations() if op.key in SUPPORTED]
    assert len(ops) == len(SUPPORTED)
    return generate_cases(ops, matrix(), dataset)


def _run(dataset, cases, mutant=None):
    return LeakSuite(ReferenceApi(dataset, mutant), dataset).run(cases)


def test_p22a_case_generation_covers_every_kind_and_principal(cases):
    kinds = {c.kind for c in cases}
    assert kinds == set(CaseKind)
    assert {c.principal_key for c in cases} == {p.key for p in matrix().principals} | {"anonymous"}
    # no allowed+visible write is ever generated (the suite never changes state)
    for c in cases:
        if c.op.method != "get" and c.principal is not None and c.principal.allows(c.op.permission):
            assert c.kind in (CaseKind.BOLA_FOREIGN_TENANT, CaseKind.BOLA_OUT_OF_SCOPE)


def test_p22a_correct_reference_passes_every_case(dataset, cases, tmp_path):
    results = _run(dataset, cases)
    s = summarize(results)
    assert s["failed"] == []
    assert s["by_status"].get("PASSED", 0) == len(cases) - s["by_status"].get("NO_DATA", 0)
    out = write_matrix_csv(results, tmp_path / "leak-matrix.csv")
    assert out.read_text(encoding="utf-8").count("\n") == len(cases) + 1


@pytest.mark.parametrize(
    "mutant,kinds",
    [
        ("anonymous", {CaseKind.ANONYMOUS}),
        ("function", {CaseKind.BFLA}),
        ("tenant", {CaseKind.BOLA_FOREIGN_TENANT, CaseKind.VISIBILITY}),
        ("company", {CaseKind.BOLA_OUT_OF_SCOPE, CaseKind.VISIBILITY}),
        ("owner", {CaseKind.BOLA_OUT_OF_SCOPE, CaseKind.VISIBILITY}),
        ("warehouse", {CaseKind.BOLA_OUT_OF_SCOPE, CaseKind.VISIBILITY}),
        ("mask", {CaseKind.OWN_OBJECT, CaseKind.VISIBILITY}),
        ("error_echo", {CaseKind.BOLA_FOREIGN_TENANT, CaseKind.BOLA_OUT_OF_SCOPE}),
    ],
)
def test_p22a_every_mutant_is_detected(dataset, cases, mutant, kinds):
    failed = [r for r in _run(dataset, cases, mutant) if r.status is Status.FAILED]
    assert failed, f"mutant {mutant!r} survived"
    assert {r.case.kind for r in failed} <= kinds | {CaseKind.VISIBILITY, CaseKind.OWN_OBJECT}
    assert {r.case.kind for r in failed} & kinds


def test_p22a_uniform_403_for_hidden_objects_is_not_a_leak(dataset, cases):
    """Control: answering 403 for every hidden object (no existence oracle) is acceptable."""
    assert summarize(_run(dataset, cases, "foreign_403"))["failed"] == []


def test_p22a_export_channel_masks_and_filters(dataset, cases):
    export = [c for c in cases if c.op.channel is Channel.EXPORT and c.kind is CaseKind.VISIBILITY]
    assert export, "inventory.issue export cases generated for principals holding *.export"
    failed = [r for r in _run(dataset, export, "warehouse") if r.status is Status.FAILED]
    assert failed or not any(c.principal.warehouse_ids for c in export)


def test_p22a_full_contract_case_generation_is_complete():
    """Over the whole contract: every non-portal operation gets an anonymous case and every principal
    a function-level decision (BFLA or a read/BOLA case)."""
    ops = [op for op in openapi.operations() if not op.portal]
    all_cases = generate_cases(ops, matrix(), Dataset())
    anon = {c.op.key for c in all_cases if c.kind is CaseKind.ANONYMOUS}
    assert anon == {op.key for op in ops}
    per_op_principals: dict[str, set[str]] = {}
    for c in all_cases:
        if c.principal is not None:
            per_op_principals.setdefault(c.op.key, set()).add(c.principal_key)
    by_key = {op.key: op for op in ops}
    missing = []
    for key, op in by_key.items():
        # collection writes the principal may perform are deliberately not executed (no state change)
        expected = {
            p.key
            for p in matrix().principals
            if not (op.method != "get" and not op.targets_one_object and p.allows(op.permission))
        }
        if per_op_principals.get(key, set()) != expected:
            missing.append(key)
    assert not missing, missing[:5]
    assert {c.op.channel for c in all_cases} >= {Channel.LIST, Channel.DETAIL, Channel.EXPORT, Channel.REPORT, Channel.AI}
