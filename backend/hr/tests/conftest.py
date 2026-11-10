"""Fixtures of the HR / employee portal tests: the IAM synthetic world (real policy engine, local OIDC issuer,
real PostgreSQL with forced RLS) plus labelled fixture parameters, a payslip source double and the
DocumentRenderer contract double (D-005: P17 proves real PDFs at G3)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

from hr.interfaces import EmployeePayslipSource
from hr.models import Employee, EmploymentContract
from hr.testing.fakes import FakePayslipSource, legal_parameters
from iam.testing.world import World
from iam.tests import conftest as iam_fixtures
from iam.tests.portal_support import onboard
from kernel import ids, services, tenancy
from kernel.interfaces.documents import DocumentRenderer
from kernel.parameters import ParameterProvider
from kernel.testing.fakes import FakeDocumentRenderer, FakeParameterProvider
from kernel.uow import unit_of_work

# pytest fixtures of the IAM suite, re-exported for the HR tests (real policy engine, local OIDC issuer)
Api = iam_fixtures.Api
_clean_cache = iam_fixtures._clean_cache
_issuer_session = iam_fixtures._issuer_session
admin = iam_fixtures.admin
audit = iam_fixtures.audit
issuer = iam_fixtures.issuer
login = iam_fixtures.login
notifications = iam_fixtures.notifications
world = iam_fixtures.world


@pytest.fixture
def parameters() -> Iterator[FakeParameterProvider]:
    fake = FakeParameterProvider(legal_parameters())
    with services.override(ParameterProvider, fake):
        yield fake


@pytest.fixture
def payslips() -> Iterator[FakePayslipSource]:
    fake = FakePayslipSource()
    with services.override(EmployeePayslipSource, fake):
        yield fake


class RecordingRenderer(FakeDocumentRenderer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def render(self, template_code: str, data: dict[str, Any], **kw: Any) -> Any:
        self.calls.append((template_code, data, kw.get("watermark")))
        return super().render(template_code, data, **kw)


@pytest.fixture
def renderer() -> Iterator[RecordingRenderer]:
    fake = RecordingRenderer()
    with services.override(DocumentRenderer, fake):
        yield fake


def make_employee(
    world: World,
    company: str,
    code: str,
    name: str,
    *,
    salary: str = "2500000",
    start: dt.date = dt.date(2024, 1, 2),
) -> Employee:
    with unit_of_work(world.ctx([company])):
        emp = Employee.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.companies[company],
            code=code,
            full_name=name,
            document_type="CC",
            document_number=str(10_000_000 + ids.new_id().int % 9_000_000),
            email=f"{code.lower()}@example.invalid",
        )
        EmploymentContract.objects.create(
            tenant_id=world.tenant_id,
            company_id=world.companies[company],
            number=f"CT-{code}",
            document_date=start,
            employee=emp,
            contract_type="INDEFINIDO",
            salary=Decimal(salary),
            start_date=start,
        )
    return emp


def add_payslip(src: FakePayslipSource, emp: Employee, period: str, net: str, **extra: Any) -> Any:
    year, month = (int(x) for x in period.split("-"))
    return src.add(
        id=ids.new_id(),
        tenant_id=emp.tenant_id,
        company_id=emp.company_id,
        employee_id=emp.id,
        period=period,
        period_end=dt.date(year, month, 28),
        net_pay=Decimal(net),
        earnings=Decimal(net) + Decimal("200000"),
        deductions=Decimal("200000"),
        legal_notice="NOT LEGALLY VERIFIED",
        parameters=(
            {"code": "smmlv", "status": "VERIFIED-PRIMARY", "synthetic": True, "legal_review_stamp": None},
            {"code": "ss.salud.empleado", "status": "VERIFIED-PRIMARY", "synthetic": True, "legal_review_stamp": None},
        ),
        **extra,
    )


@pytest.fixture
def employees_world(
    world: World, admin: str, login: Any, issuer: Any, notifications: Any, parameters: Any
) -> dict[str, Any]:
    """Two employees (A, B) of ALFA with portal accounts, one employee (C) of BETA; an HR manager user."""
    a = make_employee(world, "ALFA", "EMP-A", "Ana Pérez (sintética)")
    b = make_employee(world, "ALFA", "EMP-B", "Bruno Díaz (sintético)", salary="9800000")
    c = make_employee(world, "BETA", "EMP-C", "Carla Ruiz (sintética)")
    admin_alfa = login(admin, "ALFA")
    api_a, acc_a = onboard(world, issuer, admin_alfa, notifications, "employee", a.id, "emp-a")
    api_b, acc_b = onboard(world, issuer, admin_alfa, notifications, "employee", b.id, "emp-b")
    world.add_role("jefe_rrhh", ["hr.*.*", "timekeeping.*.*"], max_cls="sensitive-pii")
    world.add_user("jefe", ["ALFA"], subject=str(uuid.uuid4()))
    world.assign("jefe", "jefe_rrhh", "ALFA")
    return {"a": a, "b": b, "c": c, "api_a": api_a, "api_b": api_b, "acc_a": acc_a, "acc_b": acc_b,
            "jefe": login("jefe", "ALFA"), "world": world}  # fmt: skip


def portal_ctx(world: World, api: Api) -> Any:
    me = api.get("/api/portal/v1/me").json()
    return tenancy.TenantContext(world.tenant_id, frozenset({world.companies["ALFA"]}), uuid.UUID(me["id"]))
