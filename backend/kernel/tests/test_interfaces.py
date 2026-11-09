"""Contract tests for kernel.interfaces and their test doubles (T-X-QA-07 support)."""

import datetime as dt
import inspect
import typing
import uuid
from decimal import Decimal

import pytest

from kernel import interfaces, uow
from kernel.errors import Conflict, NotFound, ParameterNotVerified, ParameterUnknown, PermissionDenied, ValidationFailed
from kernel.interfaces.ar import Application, OpenItem
from kernel.interfaces.audit import AuditEntry
from kernel.interfaces.common import DocumentRef, PartyRef, Quantity
from kernel.interfaces.dimensions import DimensionNode
from kernel.interfaces.documents import LabelRequest
from kernel.interfaces.notifications import NotificationRequest
from kernel.interfaces.payroll import PayrollPayable
from kernel.interfaces.periods import PeriodInfo
from kernel.interfaces.posting import PostingLine, PostingRequest
from kernel.interfaces.reporting import ReportQuery
from kernel.interfaces.stock import StockLine
from kernel.interfaces.tax import TaxLineInput
from kernel.interfaces.treasury import PaymentStatusInfo
from kernel.models import OutboxMessage
from kernel.parameters import ParameterStatus, ParameterValue
from kernel.testing import fakes
from kernel.testing.tenancy import first_company, make_context

DOUBLES = {
    "PostingService": fakes.FakePostingService,
    "StockService": fakes.FakeInventory,
    "Inventory": fakes.FakeInventory,
    "TaxService": lambda: fakes.FakeTaxService(fakes.FakeParameterProvider()),
    "PolicyService": fakes.FakePolicyService,
    "AuditService": fakes.FakeAuditService,
    "WorkflowService": fakes.FakeWorkflowService,
    "SchedulerService": fakes.FakeSchedulerService,
    "FiscalGateway": fakes.FakeFiscal,
    "FiscalDocuments": fakes.FakeFiscal,
    "DocumentRenderer": fakes.FakeDocumentRenderer,
    "Labels": fakes.FakeLabels,
    "ReportQueryService": fakes.FakeReportQueryService,
    "NotificationService": fakes.FakeNotificationService,
    "Notifications": fakes.FakeNotificationService,
    "FileStore": fakes.FakeFileStore,
    "ParameterProvider": fakes.FakeParameterProvider,
    "PeriodService": fakes.FakePeriodService,
    "Dimensions": fakes.FakeDimensions,
    "ArOpenItems": fakes.FakeArOpenItems,
    "PaymentStatus": fakes.FakePaymentStatus,
    "PayrollPayables": fakes.FakePayrollPayables,
}


def test_T_X_QA_07_support_every_interface_is_a_typed_protocol_with_a_double():
    names = {p.__name__ for p in interfaces.CORE_INTERFACES} | set(interfaces.FEATURE_STUBS)
    names.discard("NotificationService")
    for name in names | {"NotificationService"}:
        proto = getattr(interfaces, name)
        assert getattr(proto, "_is_protocol", False), name
        assert getattr(proto, "_is_runtime_protocol", False), name
        assert isinstance(DOUBLES[name](), proto), name
        for meth_name, meth in inspect.getmembers(proto, inspect.isfunction):
            if not meth_name.startswith("_"):
                assert meth.__doc__ is None or meth.__doc__.strip(), (name, meth_name)
                hints = typing.get_type_hints(meth)
                assert "return" in hints, (name, meth_name)
    assert set(interfaces.FEATURE_STUBS) == {
        "ArOpenItems",
        "Dimensions",
        "DocumentRenderer",
        "FiscalDocuments",
        "Inventory",
        "Labels",
        "Notifications",
        "PaymentStatus",
        "PayrollPayables",
    }
    assert interfaces.Notifications is interfaces.NotificationService
    for mod in (
        "posting",
        "stock",
        "tax",
        "policy",
        "audit",
        "workflow",
        "scheduler",
        "fiscal",
        "documents",
        "reporting",
        "notifications",
        "files",
        "dimensions",
        "ar",
        "treasury",
        "payroll",
        "periods",
    ):
        doc = __import__(f"kernel.interfaces.{mod}", fromlist=["x"]).__doc__
        assert doc and len(doc.strip()) > 20, mod


def _period(company, status="OPEN"):
    return PeriodInfo(uuid.uuid4(), company, "2026-11", dt.date(2026, 11, 1), dt.date(2026, 11, 30), status)


@pytest.mark.django_db
def test_fake_periods_and_posting(ctx, company, frozen_clock):
    periods = fakes.FakePeriodService(
        [
            _period(company),
            _period(company, "CLOSED").__class__(
                uuid.uuid4(), company, "2026-10", dt.date(2026, 10, 1), dt.date(2026, 10, 31), "CLOSED"
            ),
        ]
    )
    assert periods.current_period(ctx, company).code == "2026-11"
    assert periods.current_period(ctx, uuid.uuid4()) is None
    assert _period(company).to_json()["start"] == "2026-11-01"
    posting = fakes.FakePostingService(periods)
    src = DocumentRef("sales.invoice", uuid.uuid4(), "FV-1")
    lines = (
        PostingLine("AR", "DEBIT", Decimal("119.00"), party=PartyRef(uuid.uuid4())),
        PostingLine("REVENUE", "CREDIT", Decimal("100.00")),
        PostingLine("VAT_PAYABLE", "CREDIT", Decimal("19.00"), tax_code="IVA19"),
    )
    req = PostingRequest(company, dt.date(2026, 11, 9), src, "issue", lines)
    with uow.unit_of_work(ctx) as u:
        assert posting.simulate(u, req).entry_number == "SIMULATED"
        first = posting.post(u, req)
        again = posting.post(u, req)
        assert again.replayed and again.entry_id == first.entry_id
        rev = posting.reverse(u, first.entry_id, dt.date(2026, 11, 10), "error")
        assert rev.total_debit_cop == first.total_debit_cop
        with pytest.raises(NotFound):
            posting.reverse(u, uuid.uuid4(), dt.date(2026, 11, 10), "x")
        with pytest.raises(ValidationFailed, match="unbalanced"):
            posting.post(u, PostingRequest(company, dt.date(2026, 11, 9), src, "bad", lines[:2]))
        with pytest.raises(ValidationFailed):
            posting.post(u, PostingRequest(company, dt.date(2026, 11, 9), src, "empty", ()))
        with pytest.raises(ValidationFailed):
            posting.post(
                u, PostingRequest(company, dt.date(2026, 11, 9), src, "neg", (PostingLine("X", "DEBIT", Decimal(-1)),))
            )
        with pytest.raises(Conflict) as closed:
            posting.post(u, PostingRequest(company, dt.date(2026, 10, 9), src, "late", lines))
        assert closed.value.code == "period_closed"
        with pytest.raises(NotFound):
            posting.post(u, PostingRequest(company, dt.date(2025, 1, 1), src, "none", lines))


@pytest.mark.django_db
def test_fake_inventory_contract(ctx, company):
    inv = fakes.FakeInventory()
    item, wh, wh2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    day = dt.date(2026, 11, 9)
    with uow.unit_of_work(ctx) as u:
        r = inv.receive(
            u,
            company,
            DocumentRef("grn", uuid.uuid4()),
            [StockLine("1", item, wh, Decimal(10), unit_cost=Decimal("2.5"))],
            day,
        )
        assert r.total_value == Decimal("25.0000")
        src = DocumentRef("grn", uuid.uuid4())
        inv.receive(u, company, src, [StockLine("1", item, wh, Decimal(10), unit_cost=Decimal("3.5"))], day)
        assert inv.receive(u, company, src, [], day).move_ids  # idempotent replay
        with pytest.raises(ValidationFailed):
            inv.receive(u, company, DocumentRef("grn", uuid.uuid4()), [StockLine("1", item, wh, Decimal(1))], day)
        res = inv.reserve(u, company, DocumentRef("so", uuid.uuid4()), [StockLine("1", item, wh, Decimal(5))])
        assert inv.availability(company, item, wh)[0].available == Decimal(15)
        with pytest.raises(Conflict):
            inv.issue(u, company, DocumentRef("dn", uuid.uuid4()), [StockLine("1", item, wh, Decimal(16))], day)
        with pytest.raises(Conflict):
            inv.reserve(u, company, DocumentRef("so", uuid.uuid4()), [StockLine("1", item, wh, Decimal(16))])
        inv.release(u, res)
        dn = DocumentRef("dn", uuid.uuid4())
        issued = inv.issue(u, company, dn, [StockLine("1", item, wh, Decimal(5))], day)
        assert issued.total_value == Decimal("15.0000")
        assert inv.issue(u, company, dn, [], day) == issued
        ret = inv.return_moves(u, company, DocumentRef("ret", uuid.uuid4()), dn)
        assert ret.total_value == issued.total_value and inv.on_hand[(item, wh)] == Decimal(20)
        with pytest.raises(NotFound):
            inv.return_moves(u, company, DocumentRef("ret", uuid.uuid4()), DocumentRef("x", uuid.uuid4()))
        last = inv.issue(u, company, DocumentRef("dn", uuid.uuid4()), [StockLine("1", item, wh, Decimal(20))], day)
        assert last.total_value == Decimal("60.0000") and inv.value[(item, wh)] == 0  # residual-on-zero
        inv.receive(
            u,
            company,
            DocumentRef("grn", uuid.uuid4()),
            [StockLine("1", item, wh, Decimal(4), unit_cost=Decimal(1))],
            day,
        )
        tid = inv.create_transfer(
            u, company, DocumentRef("tr", uuid.uuid4()), wh, wh2, [StockLine("1", item, wh, Decimal(2))]
        )
        assert tid in inv.transfers
        with pytest.raises(ValidationFailed):
            inv.create_transfer(u, company, DocumentRef("tr", uuid.uuid4()), wh, wh, [])
        inv.quality_hold(u, company, list(r.move_ids), "inspection")
        with pytest.raises(ValidationFailed):
            inv.quality_hold(u, company, [], "")
        assert inv.availability(company, item)[0].reserved == Decimal(2)
    neg = fakes.FakeInventory(allow_negative=True)
    with uow.unit_of_work(ctx) as u:
        neg.receive(
            u,
            company,
            DocumentRef("g", uuid.uuid4()),
            [StockLine("1", item, wh, Decimal(1), unit_cost=Decimal(1))],
            day,
        )
        neg.issue(u, company, DocumentRef("d", uuid.uuid4()), [StockLine("1", item, wh, Decimal(3))], day)
        assert neg.on_hand[(item, wh)] == Decimal(-2)


def test_fake_parameters_and_tax(company):
    day = dt.date(2026, 11, 9)
    rate = fakes.FakeParameterProvider.synthetic("tax.rate.IVA19", "0.19")
    dated = ParameterValue(
        "tax.rate.OLD", "0.16", ParameterStatus.VERIFIED_PRIMARY, "S-X", dt.date(2010, 1, 1), dt.date(2016, 12, 31)
    )
    future = ParameterValue("tax.rate.NEW", "0.20", ParameterStatus.VERIFIED_PRIMARY, "S-X", dt.date(2030, 1, 1))
    provider = fakes.FakeParameterProvider({"tax.rate.IVA19": rate, "tax.rate.OLD": dated, "tax.rate.NEW": future})
    tax = fakes.FakeTaxService(provider)
    out = tax.compute(company, "FV", day, None, [TaxLineInput("1", Decimal("100"), ("IVA19",))])
    assert out.lines[0].amount == Decimal("19.0000") and fakes.FIXTURE_LABEL in out.warnings[0]
    assert out.parameter_snapshots[0]["synthetic"] is True
    assert (
        tax.withholdings(company, "FV", day, uuid.uuid4(), [TaxLineInput("1", Decimal("100"), ("IVA19",))])
        .lines[0]
        .withholding
    )
    for code in ("UNKNOWNCODE", "OLD", "NEW"):
        with pytest.raises(ParameterUnknown):
            tax.compute(company, "FV", day, None, [TaxLineInput("1", Decimal(1), (code,))])
    disputed = fakes.FakeTaxService(
        fakes.FakeParameterProvider(
            {
                "tax.rate.D": ParameterValue(
                    "tax.rate.D", "0.05", ParameterStatus.DISPUTED, "S-D", legal_review_stamp="LR"
                )
            }
        )
    )
    assert disputed.compute(company, "FV", day, None, [TaxLineInput("1", Decimal(1), ("D",))]).warnings == (
        "D: DISPUTED",
    )
    with pytest.raises(ParameterNotVerified):
        fakes.FakeParameterProvider({"tax.rate.IVA19": rate}, production=True).get("tax.rate.IVA19", day, company)
    assert isinstance(fakes.unknown_parameter("X"), ParameterUnknown)
    assert Quantity("1.5", "KG").value == Decimal("1.5")


@pytest.mark.django_db
def test_fake_policy_audit_workflow(ctx, company):
    policy = fakes.FakePolicyService()
    assert not policy.authorize(ctx, "sales.invoice.read").allowed
    with pytest.raises(PermissionDenied):
        policy.require(ctx, "sales.invoice.read")
    policy.grant(ctx.principal_id, "kernel.sequence.read")
    assert policy.policy_version(ctx) == 2
    from kernel.models import Sequence

    Sequence.objects.create(tenant_id=ctx.tenant_id, company_id=company, sequence_code="A")
    Sequence.objects.create(tenant_id=uuid.uuid4(), company_id=company, sequence_code="B")
    assert list(
        policy.scope_queryset(ctx, "kernel.sequence.read", Sequence.objects.all()).values_list(
            "sequence_code", flat=True
        )
    ) == ["A"]
    assert policy.scope_queryset(ctx, "other.read", Sequence.objects.all()).count() == 0
    assert policy.field_mask(ctx, "x").readable == frozenset()

    audit = fakes.FakeAuditService()
    approver = make_context(tenant_id=ctx.tenant_id, company_ids=ctx.company_ids)
    wf = fakes.FakeWorkflowService()
    doc = DocumentRef("po", uuid.uuid4())
    with uow.unit_of_work(ctx) as u:
        audit.record(u, AuditEntry("create", "po", doc.id, company))
        audit.record(u, AuditEntry("update", "po", doc.id, company, {"total": "1"}))
        req = wf.request_approval(u, company, doc, "po.approval")
        assert wf.request_approval(u, company, doc, "po.approval") == req
        with pytest.raises(PermissionDenied) as sod:
            wf.decide(u, req.id, True, "self")
        assert sod.value.code == "sod_violation"
    assert audit.verify_chain(ctx.tenant_id)
    rid, tid, _entry, _ = audit.records[0]
    audit.records[0] = (rid, tid, AuditEntry("delete", "po", doc.id), audit.records[0][3])
    assert not audit.verify_chain(ctx.tenant_id)
    with uow.unit_of_work(approver) as u:
        assert wf.decide(u, req.id, True, "ok").state == "APPROVED"
        with pytest.raises(Conflict):
            wf.decide(u, req.id, False, "again")
    with pytest.raises(NotFound):
        wf.state(uuid.uuid4())


def test_fake_scheduler(ctx, company):
    sched = fakes.FakeSchedulerService()
    job = sched.schedule(ctx, company, "fx.reval", "30 2 * * *", "ledger.fx_revaluation")
    after = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)  # 07:00 Bogotá
    assert sched.next_run(job, after) == dt.datetime(2026, 11, 10, 7, 30, tzinfo=dt.UTC)
    early = dt.datetime(2026, 11, 9, 6, 0, tzinfo=dt.UTC)  # 01:00 Bogotá
    assert sched.next_run(job, early) == dt.datetime(2026, 11, 9, 7, 30, tzinfo=dt.UTC)
    with pytest.raises(ValidationFailed):
        sched.schedule(ctx, company, "bad", "*/5 * * * 1", "x")
    sched.cancel(ctx, job.id)
    assert sched.jobs == {}


def test_fake_fiscal_documents_files(ctx, company):
    fiscal = fakes.FakeFiscal()
    doc = DocumentRef("sales.invoice", uuid.uuid4(), "SETP1")
    assert fiscal.status(company, doc).status == "DRAFT"
    info = fiscal.submit(company, doc, b"<Invoice/>")
    assert info.simulated and info.unique_code.startswith("SIM-") and "SIMULADO" in info.message
    assert fiscal.submit(company, doc, b"<Invoice/>") == info and len(fiscal.history(company, doc)) == 1
    assert fiscal.poll(company, doc) == info and fiscal.mode == "FIXTURE_NOT_CERTIFIED"

    renderer = fakes.FakeDocumentRenderer()
    a = renderer.render("recibo_caja", {"n": 1}, watermark="SINTÉTICO — no válido ante la DIAN")
    assert a == renderer.render("recibo_caja", {"n": 1}, watermark="SINTÉTICO — no válido ante la DIAN")
    assert a.content.startswith(b"%PDF-FAKE") and "SINTÉTICO".encode() in a.content
    assert renderer.render("x", {}, media_type="text/html").content.startswith(b"<!--")
    labels = fakes.FakeLabels()
    job = labels.request_labels(company, [LabelRequest(uuid.uuid4(), 2)])
    assert labels.render_job(company, job).template_code == "labels"
    with pytest.raises(NotFound):
        labels.render_job(uuid.uuid4(), job)
    with pytest.raises(ValidationFailed):
        labels.request_labels(company, [LabelRequest(uuid.uuid4(), 0)])

    files = fakes.FakeFileStore()
    stored = files.put(ctx.tenant_id, "soporte.pdf", b"abc", "application/pdf")
    assert files.get(ctx.tenant_id, stored.key) == b"abc" and stored.size == 3
    with pytest.raises(NotFound):
        files.get(uuid.uuid4(), stored.key)  # other tenant
    with pytest.raises(ValidationFailed):
        files.put(ctx.tenant_id, "../x", b"", "text/plain")
    files.delete(ctx.tenant_id, stored.key)
    with pytest.raises(NotFound):
        files.delete(ctx.tenant_id, stored.key)


def test_fake_reporting(ctx):
    policy = fakes.FakePolicyService()
    data = {
        "sales": [
            {"tenant_id": ctx.tenant_id, "region": "N", "total": 1},
            {"tenant_id": ctx.tenant_id, "region": "S", "total": 2},
            {"tenant_id": uuid.uuid4(), "region": "N", "total": 99},
        ]
    }
    rep = fakes.FakeReportQueryService(data, policy)
    assert rep.models(ctx) == []
    with pytest.raises(PermissionDenied):
        rep.run(ctx, ReportQuery("sales", ("total",)))
    policy.grant(ctx.principal_id, "reporting.sales.read")
    assert rep.models(ctx) == ["sales"]
    res = rep.run(ctx, ReportQuery("sales", ("total",), ("region",), limit=1))
    assert res.columns == ("region", "total") and res.rows == (("N", 1),) and res.truncated
    assert rep.run(ctx, ReportQuery("sales", ("total",), filters={"region": "S"})).rows == ((2,),)
    assert fakes.FakeReportQueryService().models(ctx) == []


@pytest.mark.django_db
def test_fake_notifications_dimensions_ar_payments_payroll(ctx, company):
    notes = fakes.FakeNotificationService()
    with uow.unit_of_work(ctx) as u:
        notes.notify(u, NotificationRequest(uuid.uuid4(), "po.approved", company_id=company))
    assert OutboxMessage.objects.filter(event_type="notifications.notification.requested.v1").count() == 1

    group = DimensionNode(uuid.uuid4(), company, "cost_center", "100", "Admin", "AGRUPADOR", None)
    leaf = DimensionNode(uuid.uuid4(), company, "cost_center", "101", "Contab", "DETALLE", group.id)
    archived = DimensionNode(uuid.uuid4(), company, "cost_center", "102", "Old", "DETALLE", group.id, active=False)
    foreign = DimensionNode(uuid.uuid4(), uuid.uuid4(), "cost_center", "100", "X", "DETALLE", None)
    dims = fakes.FakeDimensions([group, leaf, archived, foreign])
    day = dt.date(2026, 11, 9)
    assert set(dims.subtree(company, "cost_center", group.id)) == {group.id, leaf.id, archived.id}
    dims.assert_postable(company, "cost_center", leaf.id, day)
    for node, code in [
        (group, "dimension_node_not_postable"),
        (archived, "dimension_node_archived"),
        (foreign, "dimension_cross_company"),
    ]:
        with pytest.raises(ValidationFailed) as err:
            dims.assert_postable(company, "cost_center", node.id, day)
        assert err.value.code == code
    with pytest.raises(NotFound):
        dims.get_node(company, "business_unit", leaf.id)

    party = uuid.uuid4()
    item = OpenItem(uuid.uuid4(), party, DocumentRef("FV", uuid.uuid4()), day, "COP", Decimal("100"))
    ar = fakes.FakeArOpenItems([item])
    receipt = DocumentRef("RC", uuid.uuid4())
    with uow.unit_of_work(ctx) as u:
        with pytest.raises(ValidationFailed) as over:
            ar.apply(u, company, receipt, [Application(item.id, Decimal("100.01"))])
        assert over.value.code == "overapplication"
        with pytest.raises(NotFound):
            ar.apply(u, company, receipt, [Application(uuid.uuid4(), Decimal(1))])
        ar.apply(u, company, receipt, [Application(item.id, Decimal("100"))])
        ar.apply(u, company, receipt, [Application(item.id, Decimal("100"))])  # idempotent
        assert ar.open_items(company, party) == []
        ar.unapply(u, company, receipt)
        assert ar.open_items(company, party)[0].open_amount == Decimal("100")

    pay = fakes.FakePaymentStatus()
    invoice = DocumentRef("FV", uuid.uuid4())
    info = PaymentStatusInfo(DocumentRef("RC", uuid.uuid4(), "RC-1"), "POSTED", Decimal(5), "COP", day)
    pay.add(invoice, info)
    assert pay.for_document(company, invoice) == [info] and pay.voucher(company, info.voucher.id) == info
    with pytest.raises(NotFound):
        pay.voucher(company, uuid.uuid4())

    run = DocumentRef("payroll.run", uuid.uuid4())
    payable = PayrollPayable(uuid.uuid4(), run, uuid.uuid4(), "SALARIO", Decimal("10"))
    pr = fakes.FakePayrollPayables([payable])
    ce = DocumentRef("CE", uuid.uuid4())
    with uow.unit_of_work(ctx) as u:
        assert pr.open_payables(company, run.id) == [payable]
        with pytest.raises(NotFound):
            pr.settle(u, company, ce, [uuid.uuid4()])
        pr.settle(u, company, ce, [payable.id])
        pr.settle(u, company, ce, [payable.id])
        with pytest.raises(Conflict):
            pr.settle(u, company, DocumentRef("CE", uuid.uuid4()), [payable.id])
    assert pr.open_payables(company) == []
    _ = first_company
