"""T-PAY-FORMULA-03 — an unknown legal parameter fails closed; fixture parameters are labelled
NOT LEGALLY VERIFIED on the payslip (API) — the UI half is the vitest of frontend/src/features/payroll."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from kernel.errors import DomainError
from kernel.parameters import ParameterStatus, ParameterValue
from kernel.testing.tenancy import bound
from payroll.concepts import service
from payroll.concepts.parameters import NOT_LEGALLY_VERIFIED, labelled_snapshot
from payroll.models import PayrollRun, Payslip
from payroll.testing.fixtures import DEC, MONEY, standard_user_concepts
from tax.testing.fixtures import ApiClient

pytestmark = pytest.mark.django_db

JAN_2027 = (dt.date(2027, 1, 1), dt.date(2027, 1, 31))
JAN_2026 = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))


def test_T_PAY_FORMULA_03_unknown_parameter_on_the_date_fails_closed(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.run(w.period(*JAN_2027, code="2027-01"))  # the 2026 seed ends 2026-12-31: 2027 is UNKNOWN
    with pytest.raises(DomainError) as exc:
        w.calculate(run)
    assert exc.value.code == "payroll_parameter_unknown" and exc.value.status == 422
    assert exc.value.extra["date"] == "2027-01-31"
    assert exc.value.extra["parameter"] in {"smmlv", "aux_transporte", "ss.salud.empleado"}
    with bound(w.author):  # nothing was written: still DRAFT, no payslip
        assert PayrollRun.objects.get(id=run.id).status == PayrollRun.DRAFT
        assert not Payslip.objects.filter(run_id=run.id).exists()
    graph = w.uow(lambda u: service.validate_graph(u.ctx, w.company_id, dt.date(2027, 1, 31)))
    assert graph["status"] == "invalid" and graph["data"]["errors"][0]["code"] == "payroll_parameter_unknown"


def test_T_PAY_FORMULA_03_unknown_parameter_name_rejected_on_save(payroll_world: Any) -> None:
    w = payroll_world
    f = w.formula(
        "PRIMA_LEGAL",
        "percent(salary, param_prima_2030)",
        {"salary": MONEY, "param_prima_2030": DEC},
        [{"inputs": {"salary": "100", "param_prima_2030": "10"}, "expected": "10"}],
        activate=False,
    )
    with pytest.raises(DomainError) as exc:
        w.concept("PRIMA_LEGAL", "PROVISION", formula=f)
    assert exc.value.code == "payroll_parameter_unknown" and exc.value.extra["input"] == "param_prima_2030"


def test_T_PAY_FORMULA_03_production_rules_refuse_unreviewed_parameters(payroll_world: Any, settings: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.run(w.period(*JAN_2026))
    settings.ERP_PARAMETERS_PRODUCTION_RULES = True  # no seed row carries a legal-review stamp (EG-7)
    with pytest.raises(DomainError) as exc:
        w.calculate(run)
    assert exc.value.code == "parameter_not_verified"


def test_T_PAY_FORMULA_03_fixture_parameters_labelled_not_legally_verified(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.calculate(w.run(w.period(*JAN_2026)))
    assert run.legally_verified is False
    assert run.warnings[0]["code"] == "parameters_not_legally_verified"
    slip = w.payslips(run)[0]
    assert slip.legal_notice == NOT_LEGALLY_VERIFIED and slip.legally_verified is False
    for line in slip.lines.all():
        for p in line.parameters:
            assert p["label"] == NOT_LEGALLY_VERIFIED and p["legally_verified"] is False

    # the payslip API carries the label and the parameter statuses (never masked)
    api = ApiClient(w.author)
    resp = api.get(f"/api/v1/payroll/payslips/{slip.id}", HTTP_X_COMPANY_ID=str(w.company_id))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["legal_notice"] == "NOT LEGALLY VERIFIED"
    assert {p["code"] for p in body["parameters"]} >= {"smmlv", "aux_transporte", "ss.salud.empleado"}
    assert all(p["label"] == "NOT LEGALLY VERIFIED" for p in body["parameters"])
    smmlv = next(p for p in body["parameters"] if p["code"] == "smmlv")
    assert smmlv["status"] == "DISPUTED"
    salud = next(ln for ln in body["lines"] if ln["concept"] == "SALUD_EMP")
    assert salud["parameters"][0]["label"] == "NOT LEGALLY VERIFIED" and salud["trace"]


def test_T_PAY_FORMULA_03_salary_fields_masked_without_field_grant(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.calculate(w.run(w.period(*JAN_2026)))
    from kernel.testing.tenancy import make_context

    manager = make_context(tenant_id=w.author.tenant_id, company_ids=w.author.company_ids)
    w.policy.grant(manager.principal_id, "payroll.payslip.read")  # no field grant for amounts
    api = ApiClient(manager)
    resp = api.get(f"/api/v1/payroll/payslips?run_id={run.id}", HTTP_X_COMPANY_ID=str(w.company_id))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["net_pay"] is None and item["earnings"] is None and item["lines"] is None
    assert "net_pay" in item["masked"] and item["legal_notice"] == "NOT LEGALLY VERIFIED"
    nobody = make_context(tenant_id=w.author.tenant_id, company_ids=w.author.company_ids)
    denied = ApiClient(nobody).get("/api/v1/payroll/payslips", HTTP_X_COMPANY_ID=str(w.company_id))
    assert denied.status_code == 403


def test_T_PAY_FORMULA_03_reviewed_real_value_is_not_labelled() -> None:
    reviewed = ParameterValue("smmlv", "1", ParameterStatus.VERIFIED_PRIMARY, "S-15", legal_review_stamp="x@2026")
    synthetic = ParameterValue(
        "smmlv", "1", ParameterStatus.VERIFIED_PRIMARY, "S-15", legal_review_stamp="x", synthetic=True
    )
    assert labelled_snapshot(reviewed, "param_smmlv")["label"] == ""
    assert labelled_snapshot(synthetic, "param_smmlv")["label"] == NOT_LEGALLY_VERIFIED
