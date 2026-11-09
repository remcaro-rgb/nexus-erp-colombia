"""T-P07-01 threshold routing · T-P07-02 SoD block via P04 · T-P07-03 escalation timer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from kernel import clock, ids, services
from kernel.errors import Conflict, PermissionDenied
from kernel.interfaces.common import DocumentRef
from kernel.uow import run_in_unit_of_work
from workflow.approvals.directory import ApproverDirectory, StaticDirectory
from workflow.approvals.service import DbWorkflowService, validate_steps
from workflow.models import ApprovalPolicy, Notification

pytestmark = pytest.mark.django_db
APPROVE = "workflow.approval.approve"


@pytest.fixture
def org(user_factory, company):  # type: ignore[no-untyped-def]
    d = StaticDirectory()
    people = {
        "requester": user_factory(),
        "jefe": user_factory(APPROVE),
        "gerente": user_factory(APPROVE),
        "tesorero": user_factory(APPROVE),
        "auditor": user_factory(APPROVE),
    }
    d.set_manager(company, people["requester"].principal_id, people["jefe"].principal_id)
    d.add_role(company, "gerencia", people["gerente"].principal_id)
    d.add_role(company, "tesoreria", people["tesorero"].principal_id)
    with services.override(ApproverDirectory, d):
        yield people


def _policy(ctx, company, code, steps, **kw):  # type: ignore[no-untyped-def]
    return ApprovalPolicy.objects.create(
        tenant_id=ctx.tenant_id, company_id=company, code=code, name=code, document_type="purchasing.purchase_order",
        steps=validate_steps(steps), **kw,
    )  # fmt: skip


@pytest.fixture
def policies(org, company):  # type: ignore[no-untyped-def]
    ctx = org["requester"]
    _policy(ctx, company, "OC-BAJA", [{"kind": "manager"}], max_amount=Decimal("999999.9999"), priority=10)
    _policy(ctx, company, "OC-MEDIA", [{"kind": "manager"}, {"kind": "role", "ref": "gerencia"}],
            min_amount=Decimal("1000000"), max_amount=Decimal("9999999.9999"), priority=20)
    _policy(ctx, company, "OC-ALTA", [{"kind": "manager"}, {"approvers": [{"kind": "role", "ref": "gerencia"},
            {"kind": "role", "ref": "tesoreria"}], "quorum": 2}], min_amount=Decimal("10000000"), priority=30,
            escalation_hours=24, escalate_to={"kind": "user", "ref": str(org["auditor"].principal_id)})
    _policy(ctx, company, "OC-PROVEEDOR-NUEVO", [{"kind": "role", "ref": "tesoreria"}], priority=5,
            condition="proveedor_nuevo and amount > 500000", condition_inputs={"amount": "money", "proveedor_nuevo": "boolean"})


def _request(ctx, company, amount, **facts):  # type: ignore[no-untyped-def]
    doc = DocumentRef("purchasing.purchase_order", ids.new_id(), "OC-1")
    return run_in_unit_of_work(
        lambda uow: DbWorkflowService().request(uow, company, doc, "post", facts={"amount": amount, **facts}), ctx
    ), doc


def _decide(ctx, rid, approve=True, reason="ok"):  # type: ignore[no-untyped-def]
    return run_in_unit_of_work(lambda uow: DbWorkflowService().decide(uow, rid, approve, reason), ctx)


@pytest.mark.parametrize(
    ("amount", "facts", "policy_code"),
    [
        ("500000", {"proveedor_nuevo": False}, "OC-BAJA"),
        ("999999.99", {"proveedor_nuevo": False}, "OC-BAJA"),
        ("1000000", {"proveedor_nuevo": False}, "OC-MEDIA"),
        ("9999999", {"proveedor_nuevo": False}, "OC-MEDIA"),
        ("10000000", {"proveedor_nuevo": False}, "OC-ALTA"),
        ("600000", {"proveedor_nuevo": True}, "OC-PROVEEDOR-NUEVO"),  # DSL condition, highest priority
        ("400000", {"proveedor_nuevo": True}, "OC-BAJA"),
    ],
)
def test_T_P07_01_threshold_routing(org, policies, company, amount, facts, policy_code) -> None:  # type: ignore[no-untyped-def]
    dto, _ = _request(org["requester"], company, amount, **facts)
    from workflow.models import ApprovalRequest

    assert ApprovalRequest.objects.get(id=dto.id).policy.code == policy_code


def test_T_P07_01_multi_step_routing_and_quorum(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    dto, doc = _request(org["requester"], company, "20000000", proveedor_nuevo=False)
    svc = DbWorkflowService()
    with pytest.raises(PermissionDenied):
        _decide(org["gerente"], dto.id)  # step 1 belongs to the manager
    assert _decide(org["jefe"], dto.id).state == "PENDING"
    assert _decide(org["gerente"], dto.id).state == "PENDING"  # quorum 2 of {gerencia, tesoreria}
    assert not svc.is_approved(org["requester"], doc)
    assert _decide(org["tesorero"], dto.id).state == "APPROVED"
    assert svc.is_approved(org["requester"], doc)
    # Same document again is idempotent: the approved request is returned.
    again = run_in_unit_of_work(lambda uow: svc.request(uow, company, doc, "post", facts={"amount": "20000000"}), org["requester"])
    assert again.id == dto.id and again.state == "APPROVED"


def test_T_P07_01_no_policy_means_no_approval_needed(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    doc = DocumentRef("sales.invoice", ids.new_id())
    res = run_in_unit_of_work(lambda uow: DbWorkflowService().request(uow, company, doc, "post", facts={}), org["requester"])
    assert res is None


def test_T_P07_01_modules_block_post_until_approved(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    svc = DbWorkflowService()
    doc = DocumentRef("purchasing.purchase_order", ids.new_id(), "OC-9")
    facts = {"amount": "500000", "proveedor_nuevo": False}
    with pytest.raises(Conflict) as exc:
        run_in_unit_of_work(lambda uow: svc.require_approved(uow, company, doc, "post", facts), org["requester"])
    assert exc.value.code == "approval_required"
    # The failed post rolled back the request too; a module creates it in its own submit step:
    dto = run_in_unit_of_work(lambda uow: svc.request(uow, company, doc, "post", facts=facts), org["requester"])
    _decide(org["jefe"], dto.id)
    run_in_unit_of_work(lambda uow: svc.require_approved(uow, company, doc, "post", facts), org["requester"])


def test_T_P07_02_requester_cannot_approve(org, policies, company, grants) -> None:  # type: ignore[no-untyped-def]
    requester = org["requester"]
    grants.grant(requester.principal_id, APPROVE)
    dto, _ = _request(requester, company, "500000", proveedor_nuevo=False)
    with pytest.raises(PermissionDenied) as exc:
        _decide(requester, dto.id)
    assert exc.value.code == "sod_violation"


def test_T_P07_02_sod_block_from_p04_policy(org, policies, company, grants) -> None:  # type: ignore[no-untyped-def]
    dto, _ = _request(org["requester"], company, "500000", proveedor_nuevo=False)
    grants.sod_denied.add(org["jefe"].principal_id)  # P04 reports a conflicting-duties rule
    with pytest.raises(PermissionDenied) as exc:
        _decide(org["jefe"], dto.id)
    assert exc.value.code == "sod_violation"
    grants.sod_denied.clear()
    grants.revoke(org["jefe"].principal_id, APPROVE)
    with pytest.raises(PermissionDenied) as exc2:
        _decide(org["jefe"], dto.id)
    assert exc2.value.code == "permission_denied"


def test_T_P07_02_delegation_never_reaches_the_requester(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    dto, _ = _request(org["requester"], company, "500000", proveedor_nuevo=False)
    svc = DbWorkflowService()
    with pytest.raises(PermissionDenied):
        run_in_unit_of_work(lambda uow: svc.delegate(uow, dto.id, org["requester"].principal_id, "vacaciones"), org["jefe"])
    run_in_unit_of_work(lambda uow: svc.delegate(uow, dto.id, org["auditor"].principal_id, "vacaciones"), org["jefe"])
    assert _decide(org["auditor"], dto.id).state == "APPROVED"


def test_T_P07_02_rejection_needs_reason_and_ends_request(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.errors import ValidationFailed

    dto, _ = _request(org["requester"], company, "500000", proveedor_nuevo=False)
    with pytest.raises(ValidationFailed):
        _decide(org["jefe"], dto.id, approve=False, reason=" ")
    assert _decide(org["jefe"], dto.id, approve=False, reason="sin presupuesto").state == "REJECTED"
    with pytest.raises(Conflict):
        _decide(org["jefe"], dto.id)


def test_T_P07_03_escalation_timer(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    dto, _ = _request(org["requester"], company, "20000000", proveedor_nuevo=False)
    svc = DbWorkflowService()
    auditor = org["auditor"]
    assert svc.inbox(auditor) == []
    t0 = clock.now()
    n = run_in_unit_of_work(lambda uow: svc.escalate_overdue(uow, t0 + dt.timedelta(hours=23)), org["requester"])
    assert n == 0
    n = run_in_unit_of_work(lambda uow: svc.escalate_overdue(uow, t0 + dt.timedelta(hours=25)), org["requester"])
    assert n == 1
    again = run_in_unit_of_work(lambda uow: svc.escalate_overdue(uow, t0 + dt.timedelta(hours=26)), org["requester"])
    assert again == 0  # idempotent
    assert [r.id for r in svc.inbox(auditor)] == [dto.id]
    assert Notification.objects.filter(recipient_id=auditor.principal_id, template_code="aprobacion_escalada").exists()
    assert _decide(auditor, dto.id).state == "PENDING"  # the escalation assignee may decide step 1
    kpis = svc.kpis(org["gerente"], company, t0 - dt.timedelta(days=1), t0 + dt.timedelta(days=1))
    assert kpis["pendientes"] == 1


def test_T_P07_01_approval_events_and_audit(org, policies, company) -> None:  # type: ignore[no-untyped-def]
    from kernel.models import OutboxMessage
    from workflow.audit.service import DbAuditService
    from workflow.models import AuditEvent

    dto, _ = _request(org["requester"], company, "500000", proveedor_nuevo=False)
    _decide(org["jefe"], dto.id)
    msg = OutboxMessage.objects.filter(event_type="workflow.approval.decided.v1").last()
    assert msg is not None
    actions = list(AuditEvent.objects.filter(tenant_id=org["requester"].tenant_id).values_list("action", flat=True))
    assert "workflow.approval.request" in actions and "workflow.approval.approve" in actions
    assert DbAuditService().verify(org["requester"].tenant_id).ok
