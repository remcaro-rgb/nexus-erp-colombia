"""T-PAY-FORMULA-02 — the parameter snapshot is stored; changing parameters (or formulas) later does
not change posted payrolls (C1-5: posted documents are never re-evaluated)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from typing import Any

import pytest
from django.db import DatabaseError

from kernel.errors import DomainError
from kernel.models import OutboxMessage
from kernel.testing.tenancy import bound
from payroll import runs
from payroll.models import PayrollRun, PayslipLine
from payroll.testing.fixtures import standard_user_concepts
from tax.models import Parameter
from tax.parameters import workflow as parameter_workflow

pytestmark = pytest.mark.django_db

JAN = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
FEB = (dt.date(2026, 2, 1), dt.date(2026, 2, 28))


def _change_health_rate(world: Any, new_percent: str) -> None:
    """Two-person approved correction of ss.salud.empleado (synthetic, effective for all of 2026)."""
    with bound(world.author):
        current = Parameter.objects.get(code="ss.salud.empleado", approval=Parameter.Approval.APPROVED)
    data = parameter_workflow.ProposalInput(
        code="ss.salud.empleado", value_type="percent", value=new_percent, valid_from=dt.date(2026, 1, 1),
        parameter_status="VERIFIED-PRIMARY", source_id=current.source_id, synthetic=True,
        notes="synthetic test correction",
    )  # fmt: skip
    proposal = world.uow(lambda u: parameter_workflow.propose_change(u, current.id, data, reason="test"))
    world.uow(lambda u: parameter_workflow.approve(u, proposal.id, reason="second person"), world.approver)


def test_T_PAY_FORMULA_02_parameter_snapshot_stored_and_posted_payroll_unchanged(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    emp = w.employee("2000000")
    jan = w.period(*JAN, code="2026-01")
    run = w.calculate(w.run(jan))
    posted = w.approve_and_post(run)
    assert posted.status == PayrollRun.POSTED and posted.journal_entry_id is not None

    # snapshot on the run, on the payslip and on each line (value, status, source, validity, label)
    snap = posted.parameter_snapshot["ss.salud.empleado"]
    assert snap["value"] == "0.04" and snap["status"] == "VERIFIED-PRIMARY" and snap["source_id"] == "S-36"
    assert snap["valid_from"] == "2026-01-01" and snap["label"] == "NOT LEGALLY VERIFIED"
    assert posted.parameter_snapshot["smmlv"]["status"] == "DISPUTED"
    slip = w.payslips(run)[0]
    salud = next(ln for ln in slip.lines.all() if ln.concept_code == "SALUD_EMP")
    assert salud.amount == D("80000.0000")
    assert [p["code"] for p in salud.parameters] == ["ss.salud.empleado"]
    assert salud.inputs["param_salud_empleado"] == "4.00"
    assert slip.parameter_snapshot["ss.salud.empleado"]["value"] == "0.04"

    # the journal was posted once, balanced, in the run's unit of work; the event is in the outbox
    request = next(iter(w.posting.entries.values()))
    debit = sum(ln.amount for ln in request.lines if ln.side == "DEBIT")
    credit = sum(ln.amount for ln in request.lines if ln.side == "CREDIT")
    assert debit == credit == D("2249095.0000") + D("240000.0000")
    with bound(w.author):
        assert OutboxMessage.objects.filter(event_type="payroll.run.posted.v1").count() == 1

    # later: the legal parameter changes (approved correction 4 % → 5 %) and the formula gets a new version
    _change_health_rate(w, "5")
    w.formula(
        "SALUD_EMP", "round(percent(SALARIO + BONO, param_salud_empleado), 2, 'HALF_UP') + 1",
        {"param_salud_empleado": {"type": "percent"}},
        [{"inputs": {"salary": "100", "days_worked": "30", "period_days": "30", "novelty_amount": "0",
                     "param_salud_empleado": "4"}, "expected": "5"}],
        depends_on=["SALARIO", "BONO"], effective_from=dt.date(2026, 2, 1),
    )  # fmt: skip

    # the posted payroll is unchanged: same amounts, same snapshot, same formula hash
    again = w.payslips(run)[0]
    salud_again = next(ln for ln in again.lines.all() if ln.concept_code == "SALUD_EMP")
    assert salud_again.amount == D("80000.0000")
    assert salud_again.parameters == salud.parameters and salud_again.formula_hash == salud.formula_hash
    assert again.net_pay == slip.net_pay
    with bound(w.author):
        assert PayrollRun.objects.get(id=run.id).parameter_snapshot == posted.parameter_snapshot

    # it can never be recalculated or re-simulated …
    with pytest.raises(DomainError) as locked:
        w.calculate(run)
    assert locked.value.code == "payroll_run_locked"
    with pytest.raises(DomainError) as locked_sim:
        w.uow(lambda u: runs.simulate(u, w.company_id, run.id))
    assert locked_sim.value.code == "payroll_run_locked"
    # … and the database refuses to mutate posted lines (immutability trigger)
    with pytest.raises(DatabaseError), bound(w.author):
        PayslipLine.objects.filter(id=salud.id).update(amount=D("1"))

    # a new period reads the new parameter value and the new formula version
    feb = w.period(*FEB, code="2026-02")
    feb_run = w.calculate(w.run(feb))
    feb_salud = next(ln for ln in w.payslips(feb_run)[0].lines.all() if ln.concept_code == "SALUD_EMP")
    assert feb_salud.amount == D("100001.0000")  # 5 % of 2,000,000 + 1 (version 2)
    assert feb_salud.formula_version == 2 and feb_salud.parameters[0]["value"] == "0.05"
    assert emp.employee_id == w.payslips(feb_run)[0].employee_id


def test_T_PAY_FORMULA_02_recalculation_in_calculated_state_uses_current_parameters(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.calculate(w.run(w.period(*JAN)))
    first = next(ln for ln in w.payslips(run)[0].lines.all() if ln.concept_code == "SALUD_EMP")
    _change_health_rate(w, "5")
    run = w.calculate(run)  # still CALCULATED: recalculation allowed, new snapshot
    second = next(ln for ln in w.payslips(run)[0].lines.all() if ln.concept_code == "SALUD_EMP")
    assert first.amount == D("80000.0000") and second.amount == D("100000.0000")
    assert run.parameter_snapshot["ss.salud.empleado"]["value"] == "0.05"


def test_T_PAY_FORMULA_02_approver_must_differ_from_calculator(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.calculate(w.run(w.period(*JAN)))
    with pytest.raises(DomainError) as exc:
        w.uow(lambda u: runs.approve(u, w.company_id, run.id))  # the author calculated it
    assert exc.value.code == "sod_violation"


def test_T_PAY_FORMULA_02_simulate_matches_calculation_and_persists_nothing(payroll_world: Any) -> None:
    w = payroll_world
    standard_user_concepts(w)
    w.employee("2000000")
    run = w.run(w.period(*JAN))
    sim = w.uow(lambda u: runs.simulate(u, w.company_id, run.id))
    assert sim["persisted"] is False and w.payslips(run) == []
    journal = sim["journal_entries"][0]
    assert journal["total_debit"] == journal["total_credit"] == "2489095.0000"
    calc = w.calculate(run)
    assert sim["documents"][0]["net_pay"] == str(w.payslips(calc)[0].net_pay.quantize(D("0.01")))
