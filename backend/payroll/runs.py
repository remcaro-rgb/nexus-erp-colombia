"""Payroll periods, novelties and runs: DRAFT → CALCULATED → APPROVED → POSTED → PAID.

* ``calculate`` (DRAFT/CALCULATED only, else ``payroll_run_locked``): legal parameters are read
  for the period end date (fail closed), every concept is evaluated in topological order, and the
  parameter snapshot is stored on the run, each payslip and each line.
* ``approve``: the approver is not the person who calculated the run (SoD).
* ``post``: one unit of work — payslips/lines become immutable, the balanced journal is posted
  through ``PostingService`` (journal PAYROLL) and ``payroll.run.posted.v1`` is emitted. A posted
  run is never re-evaluated: later parameter or formula changes do not touch it.
* ``simulate``: the exact calculation and journal preview, nothing persisted.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from kernel import clock, services
from kernel.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from kernel.interfaces.common import DimensionValues, DocumentRef, PartyRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.money import q_amount
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from payroll.concepts import service as concepts
from payroll.concepts.graph import compile_graph
from payroll.concepts.parameters import ParameterSet
from payroll.engine import (
    CREDIT_ROLE,
    DEFAULT_ROLE,
    NET_PAY_ROLE,
    EmployeeResult,
    NoveltyTotals,
    PeriodInfo,
    calculate_employee,
    journal_lines,
)
from payroll.errors import PayrollRunLocked
from payroll.interfaces import PayrollEmployee, PayrollEmployeeSource
from payroll.models import FREQUENCIES, PayrollConcept, PayrollNovelty, PayrollPeriod, PayrollRun, Payslip, PayslipLine
from workflow.audit.service import record_change

RECALCULABLE = (PayrollRun.DRAFT, PayrollRun.CALCULATED)


def _require(ctx: TenantContext, permission: str, company_id: uuid.UUID) -> None:
    concepts.policy().require(ctx, permission, {"company_id": company_id})


def employee_source() -> PayrollEmployeeSource:
    return services.get(PayrollEmployeeSource)


# --------------------------------------------------------------------------- periods


def period_json(p: PayrollPeriod) -> dict[str, Any]:
    return {
        "id": str(p.id), "tenant_id": str(p.tenant_id), "company_id": str(p.company_id), "code": p.code,
        "name": p.name or p.code, "status": "ACTIVE" if p.status == PayrollPeriod.OPEN else "ARCHIVED",
        "frequency": p.frequency, "start_date": p.start_date.isoformat(), "end_date": p.end_date.isoformat(),
        "version": p.version,
    }  # fmt: skip


def _date(raw: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"{field} must be a date (YYYY-MM-DD)", field=field) from exc


def create_period(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PayrollPeriod:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.period.create", company_id)
    allowed = {"code", "name", "frequency", "start_date", "end_date"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    start, end = _date(data.get("start_date"), "start_date"), _date(data.get("end_date"), "end_date")
    if end < start:
        raise ValidationFailed("end_date must not be before start_date", field="end_date")
    frequency = data.get("frequency", "MONTHLY")
    if frequency not in FREQUENCIES:
        raise ValidationFailed(f"frequency must be one of {', '.join(FREQUENCIES)}", field="frequency")
    code = str(data.get("code") or f"{start.isoformat()}-{frequency[0]}")[:40]
    if PayrollPeriod.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, code=code).exists():
        raise Conflict(f"period {code} already exists", code="duplicate")
    period = PayrollPeriod.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, code=code, name=str(data.get("name") or code)[:200],
        frequency=frequency, start_date=start, end_date=end, created_by=ctx.principal_id, updated_by=ctx.principal_id,
    )  # fmt: skip
    record_change("payroll.period.create", "payroll.period", period.id, company_id=company_id,
                  after={"code": code, "start": start.isoformat(), "end": end.isoformat()}, uow=uow)  # fmt: skip
    return period


def get_period(ctx: TenantContext, company_id: uuid.UUID, period_id: uuid.UUID) -> PayrollPeriod:
    row = PayrollPeriod.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, id=period_id).first()
    if row is None:
        raise NotFound("period not found")
    return row


# --------------------------------------------------------------------------- novelties


def novelty_json(n: PayrollNovelty) -> dict[str, Any]:
    return {
        "id": str(n.id), "tenant_id": str(n.tenant_id), "company_id": str(n.company_id),
        "employee_id": str(n.employee_id), "concept_id": str(n.concept_id), "period_id": str(n.period_id),
        "quantity": None if n.quantity is None else str(n.quantity),
        "amount": None if n.amount is None else {"amount": str(q_amount(n.amount)), "currency": "COP"},
        "version": n.version,
    }  # fmt: skip


def _money(raw: Any, field: str) -> Decimal | None:
    if raw is None:
        return None
    value = raw.get("amount") if isinstance(raw, Mapping) else raw
    if isinstance(value, (bool, float)) or value is None:
        raise ValidationFailed(f"{field} must be a decimal string", field=field)
    try:
        d = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValidationFailed(f"{field} must be a decimal string", field=field) from exc
    if not d.is_finite() or d < 0:
        raise ValidationFailed(f"{field} must be a non-negative decimal", field=field)
    return d


def create_novelty(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PayrollNovelty:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.novelty.create", company_id)
    unknown = sorted(set(data) - {"employee_id", "concept_id", "period_id", "quantity", "amount", "note"})
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    try:
        employee_id = uuid.UUID(str(data.get("employee_id")))
        concept_id = uuid.UUID(str(data.get("concept_id")))
        period_id = uuid.UUID(str(data.get("period_id")))
    except ValueError as exc:
        raise ValidationFailed("employee_id, concept_id and period_id must be UUIDs") from exc
    period = get_period(ctx, company_id, period_id)
    concept = concepts.get(ctx, company_id, concept_id)
    if PayrollRun.objects.filter(period=period, status__in=[PayrollRun.POSTED, PayrollRun.PAID]).exists():
        raise PayrollRunLocked("the period already has a posted run; novelties go to the next period")
    novelty = PayrollNovelty.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, period=period, concept=concept, employee_id=employee_id,
        quantity=_money(data.get("quantity"), "quantity"), amount=_money(data.get("amount"), "amount"),
        note=str(data.get("note") or "")[:255], created_by=ctx.principal_id, updated_by=ctx.principal_id,
    )  # fmt: skip
    record_change("payroll.novelty.create", "payroll.novelty", novelty.id, company_id=company_id,
                  after={"concept": concept.code}, sensitive=("amount",), uow=uow)  # fmt: skip
    return novelty


def _novelty_totals(period: PayrollPeriod) -> dict[uuid.UUID, dict[str, NoveltyTotals]]:
    out: dict[uuid.UUID, dict[str, NoveltyTotals]] = {}
    for n in PayrollNovelty.objects.filter(period=period).select_related("concept"):
        per = out.setdefault(n.employee_id, {})
        cur = per.get(n.concept.code, NoveltyTotals())
        per[n.concept.code] = NoveltyTotals(cur.amount + (n.amount or 0), cur.quantity + (n.quantity or 0))
    return out


# --------------------------------------------------------------------------- runs


def run_json(r: PayrollRun) -> dict[str, Any]:
    def money(v: Decimal) -> dict[str, str]:
        return {"amount": str(q_amount(v)), "currency": "COP"}

    return {
        "id": str(r.id), "tenant_id": str(r.tenant_id), "company_id": str(r.company_id), "number": r.number or None,
        "status": r.status, "period_id": str(r.period_id), "employees": list(r.employees or []),
        "calculation_date": r.calculation_date.isoformat() if r.calculation_date else None,
        "graph_order": list(r.graph_order or []), "legally_verified": r.legally_verified,
        "legal_notice": "" if r.legally_verified else "NOT LEGALLY VERIFIED",
        "parameter_snapshot": r.parameter_snapshot or {}, "warnings": list(r.warnings or []),
        "totals": {"earnings": money(r.total_earnings), "deductions": money(r.total_deductions),
                   "net_pay": money(r.total_net), "employer": money(r.total_employer)},
        "journal_entry_id": str(r.journal_entry_id) if r.journal_entry_id else None, "version": r.version,
    }  # fmt: skip


def create_run(uow: UnitOfWork, company_id: uuid.UUID, data: Mapping[str, Any]) -> PayrollRun:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.create", company_id)
    unknown = sorted(set(data) - {"period_id", "employees", "date", "currency"})
    if unknown:
        raise ValidationFailed(f"unknown properties: {', '.join(unknown)}", fields=unknown)
    try:
        period = get_period(ctx, company_id, uuid.UUID(str(data.get("period_id"))))
        employees = [str(uuid.UUID(str(e))) for e in (data.get("employees") or [])]
    except ValueError as exc:
        raise ValidationFailed("period_id and employees must be UUIDs") from exc
    count = PayrollRun.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, period=period).count()
    run = PayrollRun.objects.create(
        tenant_id=ctx.tenant_id, company_id=company_id, period=period, employees=employees,
        number=f"NOM-{period.code}-{count + 1}"[:40], created_by=ctx.principal_id, updated_by=ctx.principal_id,
    )  # fmt: skip
    record_change("payroll.run.create", "payroll.run", run.id, company_id=company_id,
                  after={"period": period.code, "number": run.number}, uow=uow)  # fmt: skip
    return run


def get_run(ctx: TenantContext, company_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False) -> PayrollRun:
    qs = PayrollRun.objects.select_for_update() if lock else PayrollRun.objects
    row = qs.select_related("period").filter(tenant_id=ctx.tenant_id, company_id=company_id, id=run_id).first()
    if row is None:
        raise NotFound("payroll run not found")
    return row


def _employees(run: PayrollRun) -> list[PayrollEmployee]:
    period = run.period
    wanted = [uuid.UUID(e) for e in run.employees] or None
    found = employee_source().employees_for_period(run.company_id, period.start_date, period.end_date, wanted)
    return sorted((e for e in found if e.company_id == run.company_id), key=lambda e: str(e.employee_id))


def compute(ctx: TenantContext, run: PayrollRun) -> tuple[list[EmployeeResult], ParameterSet, list[str]]:
    """Evaluate every employee of ``run`` (no writes). Parameters are read for the period end."""
    period = run.period
    on = period.end_date
    compiled = compile_graph(concepts.specs(ctx, run.company_id, on))
    params = ParameterSet(run.company_id, on)
    params.inputs(compiled.parameter_inputs)  # fail closed before any employee is evaluated
    info = PeriodInfo(period.start_date, period.end_date, period.frequency)
    novelties = _novelty_totals(period)
    results = [
        calculate_employee(compiled, emp, info, novelties.get(emp.employee_id, {}), params) for emp in _employees(run)
    ]
    return results, params, compiled.order


def calculate(uow: UnitOfWork, company_id: uuid.UUID, run_id: uuid.UUID) -> PayrollRun:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.update", company_id)
    run = get_run(ctx, company_id, run_id, lock=True)
    if run.status not in RECALCULABLE:
        raise PayrollRunLocked(f"run {run.number} is {run.status}; recalculation only in DRAFT/CALCULATED")
    results, params, order = compute(ctx, run)
    PayslipLine.objects.filter(payslip__run=run).delete()
    Payslip.objects.filter(run=run).delete()
    concept_ids = {
        c.code: c for c in PayrollConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
    }
    snapshot = params.snapshot()
    for r in results:
        slip = Payslip.objects.create(
            tenant_id=ctx.tenant_id, company_id=company_id, run=run, employee_id=r.employee.employee_id,
            employee_name=r.employee.full_name[:200], cost_center_id=r.employee.cost_center_id,
            earnings=r.earnings, deductions=r.deductions, employer_cost=r.employer_cost, net_pay=r.net_pay,
            parameter_snapshot=r.parameters, legally_verified=r.legally_verified, legal_notice=r.legal_notice,
        )  # fmt: skip
        PayslipLine.objects.bulk_create(
            [
                PayslipLine(
                    tenant_id=ctx.tenant_id, company_id=company_id, payslip=slip, concept=concept_ids[ln.concept_code],
                    concept_code=ln.concept_code, concept_type=ln.concept_type, sequence=ln.sequence,
                    amount=ln.amount, quantity=ln.quantity, formula_id=ln.formula_id,
                    formula_version=ln.formula_version, formula_hash=ln.formula_hash, inputs=ln.inputs,
                    trace=ln.trace, parameters=ln.parameters,
                    account_role=concept_ids[ln.concept_code].account_role or DEFAULT_ROLE[ln.concept_type],
                    electronic_payroll_code=concept_ids[ln.concept_code].electronic_payroll_code,
                )  # fmt: skip
                for ln in r.lines
                if ln.amount
            ]
        )
    run.status = PayrollRun.CALCULATED
    run.calculation_date = run.period.end_date
    run.parameter_snapshot = snapshot
    run.graph_order = order
    run.legally_verified = params.all_verified
    run.warnings = [] if params.all_verified else [
        {"code": "parameters_not_legally_verified",
         "detail": "NOT LEGALLY VERIFIED: legal parameters without a legal-review stamp (EG-7)",
         "parameters": sorted(k for k, v in snapshot.items() if not v["legally_verified"])}
    ]  # fmt: skip
    run.total_earnings = sum((r.earnings for r in results), Decimal(0))
    run.total_deductions = sum((r.deductions for r in results), Decimal(0))
    run.total_net = sum((r.net_pay for r in results), Decimal(0))
    run.total_employer = sum((r.employer_cost for r in results), Decimal(0))
    run.calculated_by, run.calculated_at = ctx.on_behalf_of or ctx.principal_id, clock.now()
    run.save_versioned(ctx.principal_id, ["status", "calculation_date", "parameter_snapshot", "graph_order",
                       "legally_verified", "warnings", "total_earnings", "total_deductions", "total_net",
                       "total_employer", "calculated_by", "calculated_at"])  # fmt: skip
    record_change("payroll.run.calculate", "payroll.run", run.id, company_id=company_id,
                  after={"status": run.status, "employees": len(results)}, uow=uow)  # fmt: skip
    return run


def _posting_request(run: PayrollRun, slips: list[Payslip]) -> PostingRequest:
    acc: dict[tuple[str, str, str, str], Decimal] = {}
    def add(role: str, side: str, cc: str, party: str, amount: Decimal) -> None:
        if amount:
            key = (role, side, cc, party)
            acc[key] = acc.get(key, Decimal(0)) + amount

    for slip in slips:
        cc = str(slip.cost_center_id or "")
        for ln in slip.lines.all():
            if ln.concept_type == "DEVENGO":
                add(ln.account_role, "DEBIT", cc, "", ln.amount)
            elif ln.concept_type == "DEDUCCION":
                add(ln.account_role, "CREDIT", cc, "", ln.amount)
            else:
                add(ln.account_role, "DEBIT", cc, "", ln.amount)
                add(CREDIT_ROLE[ln.concept_type], "CREDIT", cc, "", ln.amount)
        add(NET_PAY_ROLE, "CREDIT", cc, str(slip.employee_id), slip.net_pay)
    lines = tuple(
        PostingLine(
            account_role=role, side="DEBIT" if side == "DEBIT" else "CREDIT", amount=q_amount(amount),
            party=PartyRef(uuid.UUID(party), "employee") if party else None,
            dimensions=DimensionValues(cost_center_id=uuid.UUID(cc) if cc else None),
            memo=f"Nómina {run.number}",
        )  # fmt: skip
        for (role, side, cc, party), amount in sorted(acc.items())
    )
    return PostingRequest(
        company_id=run.company_id, posting_date=run.period.end_date,
        source=DocumentRef("payroll.run", run.id, run.number), purpose="payroll_run", lines=lines,
        journal_code="PAYROLL",
    )  # fmt: skip


def approve(uow: UnitOfWork, company_id: uuid.UUID, run_id: uuid.UUID) -> PayrollRun:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.approve", company_id)
    run = get_run(ctx, company_id, run_id, lock=True)
    if run.status != PayrollRun.CALCULATED:
        raise Conflict(f"only CALCULATED runs can be approved (status {run.status})")
    if (ctx.on_behalf_of or ctx.principal_id) == run.calculated_by:
        raise PermissionDenied("the person who calculated the run cannot approve it (SoD)", code="sod_violation")
    run.status, run.approved_by, run.approved_at = PayrollRun.APPROVED, ctx.on_behalf_of or ctx.principal_id, clock.now()
    run.save_versioned(ctx.principal_id, ["status", "approved_by", "approved_at"])
    record_change("payroll.run.approve", "payroll.run", run.id, company_id=company_id,
                  before={"status": PayrollRun.CALCULATED}, after={"status": run.status}, uow=uow)  # fmt: skip
    return run


def post(uow: UnitOfWork, company_id: uuid.UUID, run_id: uuid.UUID) -> PayrollRun:
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.post", company_id)
    run = get_run(ctx, company_id, run_id, lock=True)
    if run.status != PayrollRun.APPROVED:
        raise Conflict(f"only APPROVED runs can be posted (status {run.status})")
    slips = list(Payslip.objects.filter(run=run).prefetch_related("lines").order_by("employee_id"))
    if not slips:
        raise ValidationFailed("the run has no payslips")
    result = services.get(PostingService).post(uow, _posting_request(run, slips))
    PayslipLine.objects.filter(payslip__run=run).update(posted=True)
    Payslip.objects.filter(run=run).update(posted=True)
    run.status, run.posted_by, run.posted_at = PayrollRun.POSTED, ctx.principal_id, clock.now()
    run.journal_entry_id, run.journal_entry_number = result.entry_id, result.entry_number
    run.save_versioned(ctx.principal_id, ["status", "posted_by", "posted_at", "journal_entry_id",
                                          "journal_entry_number"])  # fmt: skip
    uow.emit("payroll.run.posted.v1", {
        "run_id": str(run.id), "period_id": str(run.period_id),
        "total_earnings": {"amount": str(q_amount(run.total_earnings)), "currency": "COP"},
        "journal_entry_id": str(result.entry_id),
    }, company_id=company_id)  # fmt: skip
    record_change("payroll.run.post", "payroll.run", run.id, company_id=company_id,
                  before={"status": PayrollRun.APPROVED}, after={"status": run.status,
                  "journal_entry": result.entry_number}, uow=uow)  # fmt: skip
    return run


def pay(uow: UnitOfWork, company_id: uuid.UUID, run_id: uuid.UUID, payment_ref: str = "") -> PayrollRun:
    """Marks the run paid; the bank file and the payment entry are P16's (PayrollPayables)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.update", company_id)
    run = get_run(ctx, company_id, run_id, lock=True)
    if run.status != PayrollRun.POSTED:
        raise Conflict(f"only POSTED runs can be paid (status {run.status})")
    run.status, run.paid_at, run.payment_ref = PayrollRun.PAID, clock.now(), payment_ref[:120]
    run.save_versioned(ctx.principal_id, ["status", "paid_at", "payment_ref"])
    record_change("payroll.run.pay", "payroll.run", run.id, company_id=company_id,
                  before={"status": PayrollRun.POSTED}, after={"status": run.status}, uow=uow)  # fmt: skip
    return run


def simulate(uow: UnitOfWork, company_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    """Would-be payslips and journal of the run; nothing is persisted (C1-3)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    _require(ctx, "payroll.run.read", company_id)
    run = get_run(ctx, company_id, run_id)
    if run.status in (PayrollRun.POSTED, PayrollRun.PAID):
        raise PayrollRunLocked("posted runs are never re-evaluated")
    results, params, order = compute(ctx, run)
    roles = {
        c.code: c.account_role or DEFAULT_ROLE[c.concept_type]
        for c in PayrollConcept.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id)
    }
    journal = journal_lines(results, roles)
    debit = sum((j["amount"] for j in journal if j["side"] == "DEBIT"), Decimal(0))
    credit = sum((j["amount"] for j in journal if j["side"] == "CREDIT"), Decimal(0))
    return {
        "persisted": False,
        "documents": [
            {
                "employee_id": str(r.employee.employee_id),
                "earnings": str(r.earnings), "deductions": str(r.deductions), "net_pay": str(r.net_pay),
                "employer_cost": str(r.employer_cost), "legal_notice": r.legal_notice,
                "lines": [{"concept": ln.concept_code, "type": ln.concept_type, "amount": str(ln.amount),
                           "formula_hash": ln.formula_hash, "trace": ln.trace} for ln in r.lines],
            }  # fmt: skip
            for r in results
        ],
        "journal_entries": [
            {
                "journal_code": "PAYROLL", "posting_date": run.period.end_date.isoformat(),
                "total_debit": str(q_amount(debit)), "total_credit": str(q_amount(credit)),
                "lines": [{**j, "amount": str(q_amount(j["amount"]))} for j in journal],
            }  # fmt: skip
        ],
        "stock_moves": [],
        "graph_order": order,
        "parameters": params.snapshot(),
        "legally_verified": params.all_verified,
    }
