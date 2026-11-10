"""S6 - Monthly payroll, one employee (FIX rates; legal golden pending EG-7) [P13, P05, P15] and
S7 - Time-keeping boundary tests (R-LB-04/05, values to confirm by legal review) [P13] (shared/11).

Every rate here is a labelled FIXTURE (NOT LEGALLY VERIFIED). The payroll journal (shared/07 §4) is
posted through the real P05 ledger in one unit of work (run + employer contributions + provisions) and
reconciled to the payroll sub-ledger liabilities (invariant 6). The dated labour parameters go through
the real ``kernel.parameters`` fail-closed rule: a 2027 SMMLV that is UNKNOWN blocks T7-6.
NominaIndividual XML / CUNE and the P13 engine are module legs (XFAIL until P13b/P15 merge).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from qa_harness import defects, golden, refdocs
from qa_harness.refdocs import Entry

D = Decimal
JUNE_END = dt.date(2026, 6, 30)


def _payroll_entries(p):
    run = [("SALARY_EXPENSE", "DEBIT", p["earned"]), ("TRANSPORT_ALLOWANCE_EXPENSE", "DEBIT", p["transport"]),
           ("PAYROLL_PAYABLE", "CREDIT", p["net"]),
           ("SS_PAYABLE_EMPLOYEE", "CREDIT", p["health_employee"] + p["pension_employee"] + p["fsp"])]
    if p["withholding"]:
        run.append(("WHT_PAYABLE", "CREDIT", p["withholding"]))
    employer_total = p["pension_employer"] + p["health_employer"] + p["ccf"] + p["arl"] + p["sena"] + p["icbf"]
    employer = [("SS_EXPENSE", "DEBIT", employer_total), ("SS_PAYABLE_AFP", "CREDIT", p["pension_employer"]),
                ("SS_PAYABLE_CCF", "CREDIT", p["ccf"]), ("SS_PAYABLE_ARL", "CREDIT", p["arl"])]
    provisions_total = p["cesantias"] + p["int_cesantias"] + p["prima"] + p["vacaciones"]
    provisions = [("BENEFITS_EXPENSE", "DEBIT", provisions_total),
                  ("PROVISION_CESANTIAS", "CREDIT", p["cesantias"]),
                  ("PROVISION_INT_CESANTIAS", "CREDIT", p["int_cesantias"]),
                  ("PROVISION_PRIMA", "CREDIT", p["prima"]), ("PROVISION_VACACIONES", "CREDIT", p["vacaciones"])]  # fmt: skip
    return [Entry("run", run), Entry("employer", employer), Entry("provisions", provisions)]


@pytest.mark.django_db
def test_p22b_S06_payroll_journal_and_subledger(andina):
    p = golden.payroll_s6()
    assert p["net"] == D(3009095)
    run = andina.run(lambda u: refdocs.post_document(
        u, doc_type="payroll.run", company=andina.a, day=JUNE_END, total=p["net"], entries=_payroll_entries(p),
        payload={"employee": "EMP-0001 (SINTÉTICO)", "period": "2026-06", "exonerated_et_114_1": True}))  # fmt: skip
    assert andina.lines(run.ref, "run") == [
        ("SALARY_EXPENSE", "Dr", D(3000000)), ("TRANSPORT_ALLOWANCE_EXPENSE", "Dr", D(249095)),
        ("PAYROLL_PAYABLE", "Cr", D(3009095)), ("SS_PAYABLE_EMPLOYEE", "Cr", D(240000)),
    ]  # fmt: skip
    assert andina.lines(run.ref, "employer") == [
        ("SS_EXPENSE", "Dr", D(495660)), ("SS_PAYABLE_AFP", "Cr", D(360000)), ("SS_PAYABLE_CCF", "Cr", D(120000)),
        ("SS_PAYABLE_ARL", "Cr", D(15660)),
    ]  # fmt: skip
    assert andina.lines(run.ref, "provisions") == [
        ("BENEFITS_EXPENSE", "Dr", D("669223.42")), ("PROVISION_CESANTIAS", "Cr", D("270757.92")),
        ("PROVISION_INT_CESANTIAS", "Cr", D("2707.58")), ("PROVISION_PRIMA", "Cr", D("270757.92")),
        ("PROVISION_VACACIONES", "Cr", D(125000)),
    ]  # fmt: skip
    # Invariant 6: GL payroll/SS/provision payables = payroll sub-ledger liabilities.
    subledger = {"PAYROLL_PAYABLE": p["net"], "SS_PAYABLE_EMPLOYEE": D(240000), "SS_PAYABLE_AFP": D(360000),
                 "SS_PAYABLE_CCF": D(120000), "SS_PAYABLE_ARL": D(15660), "PROVISION_CESANTIAS": p["cesantias"],
                 "PROVISION_INT_CESANTIAS": p["int_cesantias"], "PROVISION_PRIMA": p["prima"],
                 "PROVISION_VACACIONES": p["vacaciones"]}  # fmt: skip
    for role, amount in subledger.items():
        assert andina.balance(role, andina.a) == -amount, role


def test_p22b_S06_exoneration_and_fsp_thresholds():
    assert golden.payroll_s6()["health_employer"] == 0  # < 10 SMMLV, ET 114-1 (FIX)
    not_exonerated = golden.payroll_s6(exonerated=False)
    assert not_exonerated["health_employer"] > 0 and not_exonerated["sena"] > 0 and not_exonerated["icbf"] > 0
    high = golden.payroll_s6(salary=D(7100000))  # ≥ 4 SMMLV → FSP; > 2 SMMLV → no transport allowance
    assert high["fsp"] == D(71000) and high["transport"] == 0


def test_p22b_S06_transmission_scheduled_before_the_tenth_of_july():
    from localization_co.holidays import is_business_day

    deadline = dt.date(2026, 7, 10)  # FIX: transmission within the first 10 days of the following month
    day = dt.date(2026, 7, 1)
    while not is_business_day(day):
        day += dt.timedelta(days=1)
    assert day < deadline and is_business_day(day)


@pytest.mark.django_db
@pytest.mark.req("UR-12")
def test_p22b_S06_module_leg_payroll_engine_and_nomina_electronica(andina):
    """P13 engine computes the same run; NominaIndividual XML generated and CUNE computed (P15)."""
    defects.require("PEND-P13", "PEND-P15")


# --------------------------------------------------------------------------- S7


def _labour_parameters():
    from kernel.parameters import ParameterStatus, ParameterValue
    from kernel.testing.fakes import FakeParameterProvider

    def dated(code, value, start, end=None, status=ParameterStatus.VERIFIED_SECONDARY):
        return ParameterValue(code, value, status, "SYNTHETIC-FIXTURE", start, end, synthetic=True)

    return FakeParameterProvider({
        "smmlv": dated("smmlv", golden.FIX["smmlv.2026"], dt.date(2026, 1, 1), dt.date(2026, 12, 31),
                       ParameterStatus.DISPUTED),
        "smmlv.2027": ParameterValue("smmlv.2027", None, ParameterStatus.UNKNOWN, "SYNTHETIC-FIXTURE",
                                     dt.date(2027, 1, 1), dt.date(2027, 12, 31), synthetic=True),
    })  # fmt: skip


@pytest.mark.parametrize(("case", "day", "start", "end", "night", "surcharge", "weekly_max"), [
    ("T7-1", dt.date(2025, 12, 24), 19, 22, 1, None, 44),
    ("T7-2", dt.date(2025, 12, 25), 19, 22, 3, D("0.80"), 44),
    ("T7-3", dt.date(2026, 6, 28), 14, 22, 3, D("0.80"), 44),
    ("T7-4", dt.date(2026, 7, 5), 14, 22, 3, D("0.90"), 44),
    ("T7-5", dt.date(2026, 7, 19), 14, 22, 3, D("0.90"), 42),
])  # fmt: skip
def test_p22b_S07_boundaries(case, day, start, end, night, surcharge, weekly_max):
    from localization_co.holidays import is_holiday

    assert golden.night_hours(day, start, end) == night, case
    if case == "T7-2":
        assert is_holiday(day)  # 25 Dec: holiday surcharge applies on a weekday
    if surcharge is not None:
        assert day.weekday() == 6 or is_holiday(day), case
        assert golden.dated(golden.SUNDAY_SURCHARGE, day) == surcharge, case
    assert golden.dated(golden.WEEKLY_MAX_HOURS, day) == weekly_max, case


def test_p22b_S07_t75_amounts_with_divisor_210():
    salary = D(3000000)
    assert golden.round_cop(golden.hourly_value(salary, 210)) == D("14285.71")
    assert golden.surcharge_amount(salary, 210, 8, golden.dated(golden.SUNDAY_SURCHARGE, dt.date(2026, 7, 19))) == D(
        "102857.14"
    )
    assert golden.surcharge_amount(salary, 210, 3, golden.FIX["recargo.nocturno"]) == D("15000.00")


def test_p22b_S07_t76_2027_blocked_while_smmlv_unknown():
    from kernel.errors import ParameterUnknown

    params = _labour_parameters()
    assert golden.dated(golden.SUNDAY_SURCHARGE, dt.date(2027, 7, 4)) == D("1.00")
    with pytest.raises(ParameterUnknown) as exc:  # UNKNOWN fails closed (C1-9)
        params.get("smmlv.2027", dt.date(2027, 7, 4), golden.ZERO)
    assert exc.value.status == 422
    assert params.get("smmlv", dt.date(2026, 7, 4), golden.ZERO).requires_warning  # DISPUTED computes with a warning


@defects.xfail("DEF-P22-008")
def test_p22b_S07_t76_unknown_parameter_keeps_its_error_code():
    from kernel.errors import ParameterUnknown

    with pytest.raises(ParameterUnknown) as exc:
        _labour_parameters().get("smmlv.2027", dt.date(2027, 7, 4), golden.ZERO)
    assert exc.value.code == "parameter_unknown"
    assert exc.value.to_problem()["type"].endswith("/parameter_unknown")


def test_p22b_S07_t77_third_sunday_is_habitual():
    sundays = [dt.date(2026, 8, d) for d in (2, 9, 16)]
    assert [golden.is_habitual_sunday(i + 1) for i, _ in enumerate(sundays)] == [False, False, True]


@pytest.mark.django_db
@pytest.mark.req("UR-12")
def test_p22b_S07_module_leg_timekeeping_engine(andina):
    defects.require("PEND-P13T")
