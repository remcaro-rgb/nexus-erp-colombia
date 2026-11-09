"""Formula lifecycle (DRAFT → PENDING_APPROVAL → ACTIVE → RETIRED), SoD, snapshots and DB-backed tables."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kernel import clock, ids
from kernel.errors import Conflict, ImmutableRecord, PermissionDenied
from kernel.tenancy import PrincipalType
from kernel.testing.tenancy import make_context
from kernel.uow import run_in_unit_of_work
from workflow.formula.errors import FormulaNotApproved, FormulaTestCaseFailed, FormulaUnknownIdentifier
from workflow.formula.lifecycle import FormulaService
from workflow.models import FormulaEvaluation, FormulaTable

pytestmark = pytest.mark.django_db
AUTHOR = ("workflow.formula.create", "workflow.formula.update", "workflow.formula.read")
APPROVER = ("workflow.formula.approve", "workflow.formula.read", "workflow.formula.void")

PRICE = {
    "code": "precio_mayorista",
    "domain": "pricing",
    "expression": "round(costo * (1 + lookup('margenes', segmento, 20) / 100), 0)",
    "inputs": {
        "fields": {"costo": "money", "segmento": "string"},
        "tables": {"margenes": {"key": "string", "value": "percent"}},
    },
    "result_type": "money",
    "tests": [
        {"inputs": {"costo": "1000", "segmento": "A"}, "expected": "1300"},
        {"inputs": {"costo": "1000", "segmento": "Z"}, "expected": "1200"},
    ],
}


@pytest.fixture
def people(user_factory):  # type: ignore[no-untyped-def]
    return user_factory(*AUTHOR), user_factory(*APPROVER)


@pytest.fixture
def margins(people, company):  # type: ignore[no-untyped-def]
    author, _ = people
    return FormulaTable.objects.create(
        tenant_id=author.tenant_id,
        company_id=company,
        name="margenes",
        value_type="percent",
        rows={"A": "30", "B": "25"},
    )


def _active(people, company, data=PRICE):  # type: ignore[no-untyped-def]
    author, approver = people
    svc = FormulaService()
    row = svc.create(author, company, data)
    svc.submit(author, row.id)
    return svc.approve(approver, row.id)


def test_lifecycle_and_sod(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, approver = people
    svc = FormulaService()
    row = svc.create(author, company, PRICE)
    assert row.status == "DRAFT" and row.formula_version == 1
    report = svc.validate(author, company, PRICE, domain="pricing")
    assert report.ok and [t.passed for t in report.tests] == [True, True]
    submitted = svc.submit(author, row.id)
    assert submitted.status == "PENDING_APPROVAL" and submitted.last_test_run["ok"]
    with pytest.raises(PermissionDenied):
        svc.approve(author, row.id)  # no approve permission
    active = svc.approve(approver, row.id)
    assert active.status == "ACTIVE" and active.approved_by == approver.principal_id
    with pytest.raises(ImmutableRecord):
        svc.update(author, row.id, {"expression": "1"}, if_match=active.etag)
    v2 = svc.create(
        author,
        company,
        {
            **PRICE,
            "expression": "round(costo * 1.5, 0)",
            "tests": [{"inputs": {"costo": "1000", "segmento": "A"}, "expected": "1500"}],
        },
    )
    assert v2.formula_version == 2
    assert [v.formula_version for v in svc.versions(author, row.id)] == [1, 2]
    retired = svc.retire(approver, row.id, "reemplazada")
    assert retired.status == "RETIRED"


def test_author_cannot_approve_own_formula(user_factory, company, margins) -> None:  # type: ignore[no-untyped-def]
    both = user_factory(*AUTHOR, *APPROVER)
    svc = FormulaService()
    row = svc.create(both, company, PRICE)
    svc.submit(both, row.id)
    with pytest.raises(PermissionDenied) as exc:
        svc.approve(both, row.id)
    assert exc.value.code == "sod_violation"


def test_failing_test_cases_block_submit(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    bad = {**PRICE, "tests": [{"inputs": {"costo": "1000", "segmento": "A"}, "expected": "999"}]}
    row = FormulaService().create(author, company, bad)
    with pytest.raises(FormulaTestCaseFailed):
        FormulaService().submit(author, row.id)
    row.refresh_from_db()
    assert row.status == "DRAFT"
    no_tests = FormulaService().create(author, company, {**PRICE, "code": "sin_pruebas", "tests": []})
    with pytest.raises(FormulaTestCaseFailed):
        FormulaService().submit(author, no_tests.id)


def test_ai_agent_drafts_but_never_approves(user_factory, company, margins) -> None:  # type: ignore[no-untyped-def]
    user = user_factory(*AUTHOR)
    agent = user_factory(*AUTHOR, *APPROVER, principal_type=PrincipalType.AI_AGENT, on_behalf_of=user.principal_id)
    row = FormulaService().create(agent, company, PRICE)
    assert row.status == "DRAFT" and row.author_id == user.principal_id
    FormulaService().submit(user, row.id)
    with pytest.raises(PermissionDenied) as exc:
        FormulaService().approve(agent, row.id)
    assert exc.value.code == "human_approval_required"


def test_evaluate_for_document_stores_immutable_snapshot(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    from django.db import DatabaseError, connection

    author, _ = people
    active = _active(people, company)
    doc = ids.new_id()
    result, snap = run_in_unit_of_work(
        lambda uow: FormulaService().evaluate_for_document(
            uow,
            company,
            "pricing",
            "precio_mayorista",
            {"costo": "2000", "segmento": "B"},
            on=clock.business_date(),
            document_type="sales.invoice",
            document_id=doc,
        ),
        author,
    )
    assert result.value == Decimal("2500.0000")
    stored = FormulaEvaluation.objects.get(id=snap.id)
    assert stored.snapshot["hash"] == active.hash and stored.snapshot["tables"] == [{"name": "margenes", "version": 1}]
    with pytest.raises(DatabaseError), connection.cursor() as cur:
        cur.execute("UPDATE workflow_formula_evaluation SET code = 'x' WHERE id = %s", [str(snap.id)])


def test_unapproved_formula_cannot_be_used(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    FormulaService().create(author, company, PRICE)
    with pytest.raises(FormulaNotApproved):
        run_in_unit_of_work(
            lambda uow: FormulaService().evaluate_for_document(
                uow, company, "pricing", "precio_mayorista", {"costo": "1", "segmento": "A"}, on=clock.business_date()
            ),
            author,
        )


def test_T_FORMULA_ENGINE_05_db_tables_of_other_companies_are_invisible(people, company, grants) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    other_company = ids.new_id()
    FormulaTable.objects.create(
        tenant_id=author.tenant_id, company_id=other_company, name="margenes", value_type="percent", rows={"A": "90"}
    )
    other_tenant = make_context(company_ids=[company])
    FormulaTable.objects.create(
        tenant_id=other_tenant.tenant_id, company_id=company, name="margenes", value_type="percent", rows={"A": "80"}
    )
    report = FormulaService().validate(author, company, PRICE, domain="pricing")
    assert not report.ok  # the only "margenes" tables belong to another company / tenant
    with pytest.raises(FormulaUnknownIdentifier):
        FormulaService().simulate(author, company, PRICE, {"costo": "1000", "segmento": "A"}, domain="pricing")
    with pytest.raises(Exception) as exc:
        FormulaService().validate(author, other_company, PRICE, domain="pricing")
    assert getattr(exc.value, "code", "") in ("validation_failed", "permission_denied")


def test_dependencies_on_active_formulas_and_cycle_on_save(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    base = {
        "code": "base",
        "domain": "payroll",
        "expression": "salario / 30 * dias",
        "inputs": {"salario": "money", "dias": "integer"},
        "result_type": "money",
        "tests": [{"inputs": {"salario": "3000000", "dias": "30"}, "expected": "3000000"}],
    }
    _active(people, company, base)
    extra = {
        "code": "extra",
        "domain": "payroll",
        "expression": "round(base * 0.1, 0)",
        "depends_on": ["base"],
        "inputs": {"salario": "money", "dias": "integer"},
        "result_type": "money",
        "tests": [{"inputs": {"salario": "3000000", "dias": "15"}, "expected": "150000"}],
    }
    assert FormulaService().validate(author, company, extra, domain="payroll").ok
    cyc = {**base, "expression": "extra + 1", "depends_on": ["extra"], "inputs": {}}
    _active(people, company, extra)
    report = FormulaService().validate(author, company, cyc, domain="payroll")
    assert not report.ok and report.errors[0]["code"] == "formula_dependency_cycle"


def test_export_import_roundtrip(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    _active(people, company)
    exported = FormulaService().export(author, company)
    assert exported[0]["status"] == "ACTIVE" and exported[0]["tests"]
    other = ids.new_id()
    importer = make_context(tenant_id=author.tenant_id, company_ids=[other], principal_id=author.principal_id)
    rows = FormulaService().import_json(importer, other, exported)
    assert rows[0].status == "DRAFT" and rows[0].code == "precio_mayorista"


def test_submit_twice_conflicts(people, company, margins) -> None:  # type: ignore[no-untyped-def]
    author, _ = people
    row = FormulaService().create(author, company, PRICE)
    FormulaService().submit(author, row.id)
    with pytest.raises(Conflict):
        FormulaService().submit(author, row.id)
