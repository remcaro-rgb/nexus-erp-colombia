"""In-memory contract test doubles for every kernel interface.

They honour the documented contracts (balanced postings, no negative stock,
fail-closed parameters, SoD, tenant-namespaced files) so module tests written
against them stay meaningful when the real implementations land. Every fake
reports itself as a fixture (``FIXTURE_NOT_CERTIFIED`` / synthetic).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from kernel import clock, ids
from kernel.errors import (
    Conflict,
    NotFound,
    ParameterUnknown,
    PermissionDenied,
    ValidationFailed,
)
from kernel.interfaces.ar import Application, OpenItem
from kernel.interfaces.audit import AuditEntry
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.dimensions import DimensionNode
from kernel.interfaces.documents import LabelRequest, RenderedDocument
from kernel.interfaces.files import StoredFile
from kernel.interfaces.fiscal import FiscalStatusInfo
from kernel.interfaces.notifications import NotificationRequest
from kernel.interfaces.payroll import PayrollPayable
from kernel.interfaces.periods import PeriodInfo
from kernel.interfaces.policy import Decision, FieldMask
from kernel.interfaces.posting import PostingRequest, PostingResult
from kernel.interfaces.reporting import ReportQuery, ReportResult
from kernel.interfaces.scheduler import ScheduledJob
from kernel.interfaces.stock import Availability, StockLine, StockMoveResult
from kernel.interfaces.tax import TaxComputation, TaxLineInput, TaxLineResult
from kernel.interfaces.treasury import PaymentStatusInfo
from kernel.interfaces.workflow import ApprovalRequest
from kernel.money import q_amount
from kernel.parameters import ParameterStatus, ParameterValue, enforce
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork

FIXTURE_LABEL = "FIXTURE — NOT LEGALLY VERIFIED"


class FakePeriodService:
    def __init__(self, periods: list[PeriodInfo] | None = None) -> None:
        self.periods = list(periods or [])

    def period_for(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        for p in self.periods:
            if p.company_id == company_id and p.contains(day):
                return p
        raise NotFound("no period for date")

    def current_period(self, ctx: TenantContext, company_id: uuid.UUID) -> PeriodInfo | None:
        try:
            return self.period_for(ctx, company_id, clock.business_date())
        except NotFound:
            return None

    def assert_open(self, ctx: TenantContext, company_id: uuid.UUID, day: dt.date) -> PeriodInfo:
        p = self.period_for(ctx, company_id, day)
        if p.status != "OPEN":
            raise Conflict("period is closed", code="period_closed")
        return p


class FakePostingService:
    def __init__(self, periods: FakePeriodService | None = None) -> None:
        self.periods = periods
        self.entries: dict[uuid.UUID, PostingRequest] = {}
        self._by_source: dict[tuple[Any, ...], PostingResult] = {}
        self._n = 0

    def _validate(self, uow: UnitOfWork, request: PostingRequest) -> tuple[Decimal, Decimal]:
        uow.ctx.require_company(request.company_id)
        if self.periods is not None:
            self.periods.assert_open(uow.ctx, request.company_id, request.posting_date)
        if not request.lines:
            raise ValidationFailed("entry without lines")
        debit = credit = Decimal(0)
        for line in request.lines:
            if line.amount < 0:
                raise ValidationFailed("negative line amount")
            cop = q_amount(line.amount * line.fx_rate)
            if line.side == "DEBIT":
                debit += cop
            else:
                credit += cop
        if debit != credit:
            raise ValidationFailed("unbalanced entry", code="unbalanced_entry")
        return debit, credit

    def simulate(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        d, c = self._validate(uow, request)
        return PostingResult(uuid.UUID(int=0), "SIMULATED", d, c)

    def post(self, uow: UnitOfWork, request: PostingRequest) -> PostingResult:
        key = (request.company_id, request.source.type, request.source.id, request.purpose)
        if key in self._by_source:
            prior = self._by_source[key]
            return PostingResult(
                prior.entry_id, prior.entry_number, prior.total_debit_cop, prior.total_credit_cop, True
            )
        d, c = self._validate(uow, request)
        self._n += 1
        result = PostingResult(ids.new_id(), f"FAKE-{self._n}", d, c)
        self.entries[result.entry_id] = request
        self._by_source[key] = result
        return result

    def reverse(self, uow: UnitOfWork, entry_id: uuid.UUID, reversal_date: dt.date, reason: str) -> PostingResult:
        original = self.entries.get(entry_id)
        if original is None:
            raise NotFound("entry not found")
        flipped = tuple(
            dataclasses.replace(line, side="CREDIT" if line.side == "DEBIT" else "DEBIT") for line in original.lines
        )
        reversal = PostingRequest(
            original.company_id,
            reversal_date,
            DocumentRef("reversal", entry_id),
            f"reversal:{reason}",
            flipped,
            original.journal_code,
        )
        return self.post(uow, reversal)


class FakeInventory:
    """Implements StockService and Inventory with moving-average valuation."""

    def __init__(self, allow_negative: bool = False) -> None:
        self.allow_negative = allow_negative
        self.on_hand: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(Decimal)
        self.value: dict[tuple[uuid.UUID, uuid.UUID], Decimal] = defaultdict(Decimal)
        self.reserved: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, Decimal]] = {}
        self.moves: dict[DocumentRef, list[tuple[uuid.UUID, StockLine, Decimal]]] = {}
        self.held: set[uuid.UUID] = set()
        self.transfers: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, list[StockLine]]] = {}

    def _reserved_qty(self, key: tuple[uuid.UUID, uuid.UUID]) -> Decimal:
        return sum((q for (i, w, q) in self.reserved.values() if (i, w) == key), Decimal(0))

    def receive(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        if source in self.moves:
            return self._result(source)
        recorded = []
        for line in sorted(lines, key=lambda x: (x.item_id, x.warehouse_id, x.lot_id or uuid.UUID(int=0))):
            if line.quantity <= 0 or line.unit_cost is None:
                raise ValidationFailed("receipt needs positive quantity and unit_cost")
            key = (line.item_id, line.warehouse_id)
            val = q_amount(line.quantity * line.unit_cost)
            self.on_hand[key] += line.quantity
            self.value[key] += val
            recorded.append((ids.new_id(), line, val))
        self.moves[source] = recorded
        return self._result(source)

    def issue(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine], move_date: dt.date
    ) -> StockMoveResult:
        uow.ctx.require_company(company_id)
        if source in self.moves:
            return self._result(source)
        recorded = []
        for line in sorted(lines, key=lambda x: (x.item_id, x.warehouse_id, x.lot_id or uuid.UUID(int=0))):
            key = (line.item_id, line.warehouse_id)
            available = self.on_hand[key] - self._reserved_qty(key)
            if line.quantity > available and not self.allow_negative:
                raise Conflict("insufficient stock", code="insufficient_stock")
            qty_before = self.on_hand[key]
            val = (
                self.value[key]
                if line.quantity == qty_before
                else q_amount(self.value[key] / qty_before * line.quantity)
            )
            self.on_hand[key] -= line.quantity
            self.value[key] -= val
            recorded.append((ids.new_id(), line, -val))
        self.moves[source] = recorded
        return self._result(source)

    def return_moves(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, original: DocumentRef
    ) -> StockMoveResult:
        if original not in self.moves:
            raise NotFound("original moves not found")
        if source in self.moves:
            return self._result(source)
        recorded = []
        for _mid, line, val in self.moves[original]:
            key = (line.item_id, line.warehouse_id)
            sign = Decimal(-1) if val >= 0 else Decimal(1)
            self.on_hand[key] += sign * line.quantity
            self.value[key] -= val
            recorded.append((ids.new_id(), line, -val))
        self.moves[source] = recorded
        return self._result(source)

    def _result(self, source: DocumentRef) -> StockMoveResult:
        rec = self.moves[source]
        return StockMoveResult(tuple(m for m, _, _ in rec), sum((abs(v) for _, _, v in rec), Decimal(0)))

    def availability(
        self, company_id: uuid.UUID, item_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> list[Availability]:
        out = []
        for (i, w), qty in sorted(self.on_hand.items()):
            if i == item_id and (warehouse_id is None or w == warehouse_id):
                res = self._reserved_qty((i, w))
                out.append(Availability(i, w, qty, res, qty - res))
        return out

    def reserve(
        self, uow: UnitOfWork, company_id: uuid.UUID, source: DocumentRef, lines: list[StockLine]
    ) -> list[uuid.UUID]:
        uow.ctx.require_company(company_id)
        out = []
        for line in lines:
            key = (line.item_id, line.warehouse_id)
            if line.quantity > self.on_hand[key] - self._reserved_qty(key):
                raise Conflict("insufficient stock", code="insufficient_stock")
            rid = ids.new_id()
            self.reserved[rid] = (line.item_id, line.warehouse_id, line.quantity)
            out.append(rid)
        return out

    def release(self, uow: UnitOfWork, reservation_ids: list[uuid.UUID]) -> None:
        for rid in reservation_ids:
            self.reserved.pop(rid, None)

    def create_transfer(
        self,
        uow: UnitOfWork,
        company_id: uuid.UUID,
        source: DocumentRef,
        from_warehouse: uuid.UUID,
        to_warehouse: uuid.UUID,
        lines: list[StockLine],
    ) -> uuid.UUID:
        if from_warehouse == to_warehouse:
            raise ValidationFailed("transfer needs two different warehouses")
        self.reserve(uow, company_id, source, lines)
        tid = ids.new_id()
        self.transfers[tid] = (from_warehouse, to_warehouse, lines)
        return tid

    def quality_hold(self, uow: UnitOfWork, company_id: uuid.UUID, move_ids: list[uuid.UUID], reason: str) -> None:
        if not reason:
            raise ValidationFailed("reason required")
        self.held.update(move_ids)


class FakeParameterProvider:
    """Labelled synthetic parameters (NOT LEGALLY VERIFIED); missing → UNKNOWN."""

    def __init__(self, values: dict[str, ParameterValue] | None = None, production: bool = False) -> None:
        self.values = dict(values or {})
        self.production = production

    def get(self, code: str, at_date: dt.date, company: uuid.UUID) -> ParameterValue:
        value = self.values.get(code)
        if value is not None and value.valid_from and at_date < value.valid_from:
            value = None
        if value is not None and value.valid_to and at_date > value.valid_to:
            value = None
        return enforce(value, code, production=self.production)

    @staticmethod
    def synthetic(code: str, value: Any, status: ParameterStatus = ParameterStatus.VERIFIED_PRIMARY) -> ParameterValue:
        return ParameterValue(code, value, status, source_id="SYNTHETIC-FIXTURE", synthetic=True)


class FakeTaxService:
    """Rates come only from the parameter provider (``tax.rate.<code>``)."""

    def __init__(self, parameters: FakeParameterProvider) -> None:
        self.parameters = parameters

    def _compute(
        self, company_id: uuid.UUID, document_date: dt.date, lines: list[TaxLineInput], withholding: bool
    ) -> TaxComputation:
        results, snaps, warns = [], [], []
        for line in lines:
            for code in line.tax_codes:
                param = self.parameters.get(f"tax.rate.{code}", document_date, company_id)
                rate = Decimal(str(param.value))
                results.append(
                    TaxLineResult(
                        line.line_ref, code, line.base_amount, rate, q_amount(line.base_amount * rate), withholding
                    )
                )
                snaps.append(param.snapshot())
                if param.requires_warning:
                    warns.append(f"{code}: {FIXTURE_LABEL}" if param.synthetic else f"{code}: DISPUTED")
        return TaxComputation(tuple(results), tuple(snaps), tuple(warns))

    def compute(
        self,
        company_id: uuid.UUID,
        document_type: str,
        document_date: dt.date,
        party_id: uuid.UUID | None,
        lines: list[TaxLineInput],
    ) -> TaxComputation:
        return self._compute(company_id, document_date, lines, False)

    def withholdings(
        self,
        company_id: uuid.UUID,
        document_type: str,
        document_date: dt.date,
        party_id: uuid.UUID,
        lines: list[TaxLineInput],
    ) -> TaxComputation:
        return self._compute(company_id, document_date, lines, True)


class FakePolicyService:
    """Deny by default; grants are explicit ``(principal_id, action)`` pairs."""

    def __init__(self, grants: set[tuple[uuid.UUID, str]] | None = None) -> None:
        self.grants = set(grants or set())
        self.version = 1

    def grant(self, principal_id: uuid.UUID, action: str) -> None:
        self.grants.add((principal_id, action))
        self.version += 1

    def authorize(self, ctx: TenantContext, action: str, resource: Any = None) -> Decision:
        allowed = (ctx.principal_id, action) in self.grants
        return Decision(allowed, "granted" if allowed else "deny_by_default")

    def require(self, ctx: TenantContext, action: str, resource: Any = None) -> None:
        if not self.authorize(ctx, action, resource).allowed:
            raise PermissionDenied(f"missing permission {action}")

    def scope_queryset(self, ctx: TenantContext, action: str, queryset: Any) -> Any:
        if not self.authorize(ctx, action).allowed:
            return queryset.none()
        return queryset.filter(tenant_id=ctx.tenant_id, company_id__in=list(ctx.company_ids))

    def field_mask(self, ctx: TenantContext, resource_type: str) -> FieldMask:
        return FieldMask(frozenset(), frozenset())

    def policy_version(self, ctx: TenantContext) -> int:
        return self.version


class FakeAuditService:
    def __init__(self) -> None:
        self.records: list[tuple[uuid.UUID, uuid.UUID, AuditEntry, str]] = []

    def _hash(self, prev: str, entry: AuditEntry) -> str:
        return hashlib.sha256(f"{prev}|{entry.action}|{entry.resource_type}|{entry.resource_id}".encode()).hexdigest()

    def record(self, uow: UnitOfWork, entry: AuditEntry) -> uuid.UUID:
        prev = self.records[-1][3] if self.records else ""
        rid = ids.new_id()
        self.records.append((rid, uow.ctx.tenant_id, entry, self._hash(prev, entry)))
        return rid

    def verify_chain(self, tenant_id: uuid.UUID) -> bool:
        prev = ""
        for _rid, _tid, entry, digest in self.records:
            if self._hash(prev, entry) != digest:
                return False
            prev = digest
        return True


class FakeWorkflowService:
    def __init__(self) -> None:
        self.requests: dict[uuid.UUID, ApprovalRequest] = {}

    def request_approval(
        self, uow: UnitOfWork, company_id: uuid.UUID, document: DocumentRef, policy_code: str, reason: str = ""
    ) -> ApprovalRequest:
        for r in self.requests.values():
            if r.document == document and r.state == "PENDING":
                return r
        req = ApprovalRequest(ids.new_id(), document, "PENDING", uow.ctx.principal_id)
        self.requests[req.id] = req
        return req

    def decide(self, uow: UnitOfWork, request_id: uuid.UUID, approve: bool, reason: str) -> ApprovalRequest:
        req = self.state(request_id)
        if req.requested_by == uow.ctx.principal_id:
            raise PermissionDenied("requester cannot approve (SoD)", code="sod_violation")
        if req.state != "PENDING":
            raise Conflict("already decided")
        new = ApprovalRequest(req.id, req.document, "APPROVED" if approve else "REJECTED", req.requested_by)
        self.requests[req.id] = new
        return new

    def state(self, request_id: uuid.UUID) -> ApprovalRequest:
        try:
            return self.requests[request_id]
        except KeyError:
            raise NotFound("approval request not found") from None


class FakeSchedulerService:
    """Supports ``@daily HH:MM`` style crons only (``M H * * *``)."""

    def __init__(self) -> None:
        self.jobs: dict[uuid.UUID, ScheduledJob] = {}

    def schedule(
        self,
        ctx: TenantContext,
        company_id: uuid.UUID,
        code: str,
        cron: str,
        task_name: str,
        args: dict[str, Any] | None = None,
    ) -> ScheduledJob:
        ctx.require_company(company_id)
        job = ScheduledJob(ids.new_id(), code, cron, "America/Bogota", task_name, dict(args or {}))
        self.next_run(job, clock.now())
        self.jobs[job.id] = job
        return job

    def cancel(self, ctx: TenantContext, job_id: uuid.UUID) -> None:
        self.jobs.pop(job_id, None)

    def next_run(self, job: ScheduledJob, after: dt.datetime) -> dt.datetime:
        parts = job.cron.split()
        if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
            raise ValidationFailed("fake scheduler supports 'M H * * *' only")
        minute, hour = int(parts[0]), int(parts[1])
        local = after.astimezone(clock.tz(job.timezone))
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += dt.timedelta(days=1)
        return candidate.astimezone(dt.UTC)


class FakeFiscal:
    """FiscalGateway + FiscalDocuments fixture (FIXTURE_NOT_CERTIFIED, simulated)."""

    mode = "FIXTURE_NOT_CERTIFIED"

    def __init__(self) -> None:
        self.log: dict[DocumentRef, list[FiscalStatusInfo]] = defaultdict(list)

    def submit(self, company_id: uuid.UUID, document: DocumentRef, payload: bytes) -> FiscalStatusInfo:
        existing = self.log.get(document)
        if existing and existing[-1].status == "ACCEPTED":
            return existing[-1]
        code = "SIM-" + hashlib.sha384(payload).hexdigest()
        info = FiscalStatusInfo(document, "ACCEPTED", code, simulated=True, message="SIMULADO — NO CERTIFICADO")
        self.log[document].append(info)
        return info

    def poll(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo:
        return self.status(company_id, document)

    def status(self, company_id: uuid.UUID, document: DocumentRef) -> FiscalStatusInfo:
        hist = self.log.get(document)
        if not hist:
            return FiscalStatusInfo(document, "DRAFT", None, simulated=True)
        return hist[-1]

    def history(self, company_id: uuid.UUID, document: DocumentRef) -> list[FiscalStatusInfo]:
        return list(self.log.get(document, []))


class FakeDocumentRenderer:
    """Deterministic pseudo-PDF bytes (not a real PDF; G3 proves real PDFs)."""

    def render(
        self,
        template_code: str,
        data: dict[str, Any],
        *,
        locale: str = "es-CO",
        media_type: str = "application/pdf",
        watermark: str | None = None,
    ) -> RenderedDocument:
        body = repr(sorted((str(k), str(v)) for k, v in data.items())).encode()
        header = b"%PDF-FAKE " if media_type == "application/pdf" else b"<!-- fake -->"
        mark = (watermark or "").encode()
        return RenderedDocument(
            header + template_code.encode() + b"\n" + mark + b"\n" + body,
            media_type,
            template_code,
            "fake-1",
            watermark,
        )


class FakeLabels:
    def __init__(self, renderer: FakeDocumentRenderer | None = None) -> None:
        self.renderer = renderer or FakeDocumentRenderer()
        self.jobs: dict[uuid.UUID, tuple[uuid.UUID, list[LabelRequest]]] = {}

    def request_labels(self, company_id: uuid.UUID, requests: list[LabelRequest]) -> uuid.UUID:
        if not requests or any(r.copies < 1 for r in requests):
            raise ValidationFailed("at least one label with copies >= 1")
        jid = ids.new_id()
        self.jobs[jid] = (company_id, list(requests))
        return jid

    def render_job(self, company_id: uuid.UUID, job_id: uuid.UUID) -> RenderedDocument:
        job = self.jobs.get(job_id)
        if job is None or job[0] != company_id:
            raise NotFound("label job not found")
        data = {str(i): f"{r.item_id}x{r.copies}" for i, r in enumerate(job[1])}
        return self.renderer.render("labels", data)


class FakeReportQueryService:
    def __init__(
        self, data: dict[str, list[dict[str, Any]]] | None = None, policy: FakePolicyService | None = None
    ) -> None:
        self.data = data or {}
        self.policy = policy or FakePolicyService()

    def models(self, ctx: TenantContext) -> list[str]:
        return sorted(m for m in self.data if self.policy.authorize(ctx, f"reporting.{m}.read").allowed)

    def run(self, ctx: TenantContext, query: ReportQuery) -> ReportResult:
        self.policy.require(ctx, f"reporting.{query.model}.read")
        rows = [
            r
            for r in self.data.get(query.model, [])
            if str(r.get("tenant_id")) == str(ctx.tenant_id) and all(r.get(k) == v for k, v in query.filters.items())
        ]
        cols = query.dimensions + query.measures
        out = tuple(tuple(r.get(c) for c in cols) for r in rows[: query.limit])
        return ReportResult(cols, out, truncated=len(rows) > query.limit)


class FakeNotificationService:
    def __init__(self) -> None:
        self.sent: list[tuple[uuid.UUID, NotificationRequest]] = []

    def notify(self, uow: UnitOfWork, request: NotificationRequest) -> uuid.UUID:
        nid = ids.new_id()
        uow.emit(
            "notifications.notification.requested.v1",
            {"notification_id": str(nid), "template": request.template_code, "recipient_id": str(request.recipient_id)},
            company_id=request.company_id,
        )
        self.sent.append((nid, request))
        return nid


class FakeFileStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, tenant_id: uuid.UUID, name: str, content: bytes, media_type: str) -> StoredFile:
        if "/" in name or ".." in name:
            raise ValidationFailed("invalid file name")
        key = f"{tenant_id}/{ids.new_id()}/{name}"
        self.objects[key] = (content, media_type)
        return StoredFile(key, len(content), hashlib.sha256(content).hexdigest(), media_type)

    def _check(self, tenant_id: uuid.UUID, key: str) -> None:
        if not key.startswith(f"{tenant_id}/") or key not in self.objects:
            raise NotFound("file not found")

    def get(self, tenant_id: uuid.UUID, key: str) -> bytes:
        self._check(tenant_id, key)
        return self.objects[key][0]

    def delete(self, tenant_id: uuid.UUID, key: str) -> None:
        self._check(tenant_id, key)
        del self.objects[key]


class FakeDimensions:
    def __init__(self, nodes: list[DimensionNode] | None = None) -> None:
        self.nodes = {n.id: n for n in nodes or []}

    def get_node(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID) -> DimensionNode:
        n = self.nodes.get(node_id)
        if n is None or n.company_id != company_id or n.dimension != dimension:
            raise NotFound("dimension node not found")
        return n

    def subtree(
        self, company_id: uuid.UUID, dimension: str, root_id: uuid.UUID, valid_on: dt.date | None = None
    ) -> list[uuid.UUID]:
        self.get_node(company_id, dimension, root_id)
        out, frontier = [root_id], [root_id]
        while frontier:
            parent = frontier.pop()
            children = [n.id for n in self.nodes.values() if n.parent_id == parent]
            out.extend(children)
            frontier.extend(children)
        return out

    def assert_postable(self, company_id: uuid.UUID, dimension: str, node_id: uuid.UUID, on: dt.date) -> None:
        n = self.nodes.get(node_id)
        if n is not None and n.company_id != company_id:
            raise ValidationFailed("cross-company dimension", code="dimension_cross_company")
        n = self.get_node(company_id, dimension, node_id)
        if n.node_type != "DETALLE":
            raise ValidationFailed("group node is not postable", code="dimension_node_not_postable")
        if not n.active:
            raise ValidationFailed("archived dimension node", code="dimension_node_archived")


class FakeArOpenItems:
    def __init__(self, items: list[OpenItem] | None = None) -> None:
        self.items = {i.id: i for i in items or []}
        self.applied: dict[DocumentRef, list[Application]] = {}

    def open_items(self, company_id: uuid.UUID, party_id: uuid.UUID) -> list[OpenItem]:
        return [i for i in self.items.values() if i.party_id == party_id and i.open_amount > 0]

    def apply(
        self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef, applications: list[Application]
    ) -> None:
        if receipt in self.applied:
            return
        for a in applications:
            item = self.items.get(a.open_item_id)
            if item is None:
                raise NotFound("open item not found")
            if a.amount <= 0 or a.amount > item.open_amount:
                raise ValidationFailed("amount exceeds open amount", code="overapplication")
        for a in applications:
            item = self.items[a.open_item_id]
            self.items[item.id] = OpenItem(
                item.id, item.party_id, item.document, item.due_date, item.currency, item.open_amount - a.amount
            )
        self.applied[receipt] = list(applications)

    def unapply(self, uow: UnitOfWork, company_id: uuid.UUID, receipt: DocumentRef) -> None:
        for a in self.applied.pop(receipt, []):
            item = self.items[a.open_item_id]
            self.items[item.id] = OpenItem(
                item.id, item.party_id, item.document, item.due_date, item.currency, item.open_amount + a.amount
            )


class FakePaymentStatus:
    def __init__(self) -> None:
        self.vouchers: dict[uuid.UUID, tuple[DocumentRef, PaymentStatusInfo]] = {}

    def add(self, document: DocumentRef, info: PaymentStatusInfo) -> None:
        self.vouchers[info.voucher.id] = (document, info)

    def for_document(self, company_id: uuid.UUID, document: DocumentRef) -> list[PaymentStatusInfo]:
        return [info for doc, info in self.vouchers.values() if doc == document]

    def voucher(self, company_id: uuid.UUID, voucher_id: uuid.UUID) -> PaymentStatusInfo:
        try:
            return self.vouchers[voucher_id][1]
        except KeyError:
            raise NotFound("voucher not found") from None


class FakePayrollPayables:
    def __init__(self, payables: list[PayrollPayable] | None = None) -> None:
        self.payables = {p.id: p for p in payables or []}
        self.settled: dict[uuid.UUID, DocumentRef] = {}

    def open_payables(self, company_id: uuid.UUID, run_id: uuid.UUID | None = None) -> list[PayrollPayable]:
        return [
            p for p in self.payables.values() if p.id not in self.settled and (run_id is None or p.run.id == run_id)
        ]

    def settle(
        self, uow: UnitOfWork, company_id: uuid.UUID, payment: DocumentRef, payable_ids: list[uuid.UUID]
    ) -> None:
        for pid in payable_ids:
            if pid not in self.payables:
                raise NotFound("payable not found")
            if pid in self.settled and self.settled[pid] != payment:
                raise Conflict("payable already settled")
        for pid in payable_ids:
            self.settled[pid] = payment


def unknown_parameter(code: str) -> ParameterUnknown:
    return ParameterUnknown(f"parameter {code} is UNKNOWN", code=code)
