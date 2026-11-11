"""Treasury endpoints (contract ``contracts/openapi/modules/treasury.yaml``, P16a core).

Each handler authorizes with the operation's ``x-permission`` (the services call ``PolicyService``), reads inside
the request's forced-RLS scope and writes in one idempotent unit of work (``Idempotency-Key``; ``If-Match`` on
PATCH/DELETE). Foreign / out-of-scope ids answer 404.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

from django.db.models import QuerySet
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from kernel.errors import NotFound, ValidationFailed
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from treasury import kpis
from treasury.api import serializers as ser
from treasury.api.base import TreasuryView, action_body, body, check_if_match, command, page
from treasury.batches import service as batches
from treasury.models import (
    BankAccount,
    BankStatement,
    CashBox,
    Fund,
    PaymentBatch,
    StatementLine,
    TreasuryConcept,
    TreasuryVoucher,
)
from treasury.reconciliation import service as recon
from treasury.services import masters
from treasury.services.common import get_scoped, scoped, uuid_in
from treasury.vouchers import funds
from treasury.vouchers import service as vouchers


def _etag(model: Any) -> Callable[[Any], str | None]:
    def tag(data: Any) -> str | None:
        if isinstance(data, dict) and "id" in data and "version" in data:
            return str(model(pk=uuid.UUID(data["id"]), version=data["version"]).etag)
        return None

    return tag


def _with_etag(data: Any, obj: Any) -> Response:
    response = Response(data)
    response["ETag"] = obj.etag
    return response


# --------------------------------------------------------------------------- generic master CRUD


class _MasterList(TreasuryView):
    model: ClassVar[Any]
    perm: ClassVar[str]
    filters: ClassVar[dict[str, str]] = {"status": "status", "code": "code"}

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        raise NotImplementedError

    def create(self, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, f"{self.perm}.read", self.model.objects.filter(company_id=company))
        return page(request, qs, lambda o: self.to_json(ctx, o), self.filters)

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            f"{self.perm}.create",
            lambda u: (self.to_json(ctx, self.create(u, company, body(request))), 201),
            etag=_etag(self.model),
        )


class _MasterDetail(TreasuryView):
    model: ClassVar[Any]
    perm: ClassVar[str]
    resource: ClassVar[str]

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        raise NotImplementedError

    def update(self, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _obj(self, ctx: TenantContext, company: uuid.UUID, action: str, pk: uuid.UUID) -> Any:
        return get_scoped(ctx, f"{self.perm}.{action}", self.model.objects.filter(company_id=company), pk)

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self._obj(ctx, company, "read", pk)
        return _with_etag(self.to_json(ctx, obj), obj)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self._obj(ctx, company, "update", pk)
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            f"{self.perm}.update",
            lambda u: (self.to_json(ctx, self.update(u, obj, body(request))), 200),
            etag=_etag(self.model),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self._obj(ctx, company, "delete", pk)
        check_if_match(request, obj, required=True)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            masters.archive(u, obj, f"{self.perm}.delete", self.resource)
            return None, 204

        return command(request, ctx, f"{self.perm}.delete", fn)


class FundList(_MasterList):
    model, perm = Fund, "treasury.fund"
    filters: ClassVar[dict[str, str]] = {"status": "status", "code": "code", "fund_type": "fund_type"}

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.fund_json(obj)

    def create(self, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        return masters.create_fund(uow, company, data)


class FundDetail(_MasterDetail):
    model, perm, resource = Fund, "treasury.fund", "treasury.fund"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.fund_json(obj)

    def update(self, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return masters.update_fund(uow, obj, data)


class FundSnapshot(TreasuryView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        fund = get_scoped(ctx, "treasury.fund.read", Fund.objects.filter(company_id=company), pk)
        snap = funds.snapshot(ctx, fund)
        return Response(
            {
                "report": "treasury.fund_snapshot",
                "parameters": {"fund_id": str(fund.id)},
                "columns": [
                    {"key": "fund_code", "label": "Fondo", "type": "string"},
                    {"key": "book_balance", "label": "Saldo en libros", "type": "money"},
                    {"key": "unreconciled", "label": "Sin conciliar", "type": "money"},
                ],
                "rows": [snap],
                "totals": {"book_balance": snap["book_balance"], "unreconciled": snap["unreconciled"]},
                "synthetic": False,
            }
        )


class BankAccountList(_MasterList):
    model, perm = BankAccount, "treasury.bank_account"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.bank_account_json(ctx, obj)

    def create(self, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        return masters.create_bank_account(uow, company, data)


class BankAccountDetail(_MasterDetail):
    model, perm, resource = BankAccount, "treasury.bank_account", "treasury.bank_account"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.bank_account_json(ctx, obj)

    def update(self, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return masters.update_bank_account(uow, obj, data)


class CashBoxList(_MasterList):
    model, perm = CashBox, "treasury.cash_box"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.cash_box_json(obj)

    def create(self, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        return masters.create_cash_box(uow, company, data)


class CashBoxDetail(_MasterDetail):
    model, perm, resource = CashBox, "treasury.cash_box", "treasury.cash_box"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.cash_box_json(obj)

    def update(self, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return masters.update_cash_box(uow, obj, data)


class ConceptList(_MasterList):
    model, perm = TreasuryConcept, "treasury.concept"
    filters: ClassVar[dict[str, str]] = {"status": "status", "code": "code", "direction": "direction"}

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.concept_json(obj)

    def create(self, uow: UnitOfWork, company: uuid.UUID, data: dict[str, Any]) -> Any:
        return masters.create_concept(uow, company, data)


class ConceptDetail(_MasterDetail):
    model, perm, resource = TreasuryConcept, "treasury.concept", "treasury.concept"

    def to_json(self, ctx: TenantContext, obj: Any) -> dict[str, Any]:
        return ser.concept_json(obj)

    def update(self, uow: UnitOfWork, obj: Any, data: dict[str, Any]) -> Any:
        return masters.update_concept(uow, obj, data)


# --------------------------------------------------------------------------- vouchers (RC / CE)

VOUCHER_FILTERS = {
    "status": "status",
    "number": "number",
    "fund": "fund_id",
    "party": "party_id",
    "date": "document_date",
}


class _Voucher(TreasuryView):
    voucher_type: ClassVar[str]
    perm: ClassVar[str]

    def qs(self, company: uuid.UUID) -> QuerySet[Any]:
        return TreasuryVoucher.objects.filter(company_id=company, voucher_type=self.voucher_type)

    def one(self, ctx: TenantContext, company: uuid.UUID, action: str, pk: uuid.UUID) -> TreasuryVoucher:
        obj: TreasuryVoucher = get_scoped(ctx, f"{self.perm}.{action}", self.qs(company), pk)
        return obj


class VoucherList(_Voucher):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, f"{self.perm}.read", self.qs(company))
        return page(request, qs, lambda v: ser.voucher_json(ctx, v), VOUCHER_FILTERS)

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)

        def fn(uow: UnitOfWork) -> tuple[Any, int]:
            created = vouchers.create_voucher(uow, company, self.voucher_type, body(request))
            return ser.voucher_json(ctx, created), 201

        return command(request, ctx, f"{self.perm}.create", fn, etag=_etag(TreasuryVoucher))


class VoucherDetail(_Voucher):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self.one(ctx, company, "read", pk)
        return _with_etag(ser.voucher_json(ctx, obj), obj)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self.one(ctx, company, "update", pk)
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            f"{self.perm}.update",
            lambda u: (ser.voucher_json(ctx, vouchers.update_voucher(u, obj, body(request))), 200),
            etag=_etag(TreasuryVoucher),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self.one(ctx, company, "delete", pk)
        check_if_match(request, obj, required=True)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            vouchers.cancel_voucher(u, obj)
            return None, 204

        return command(request, ctx, f"{self.perm}.delete", fn)


ACTION_PERM = {
    "submit": "update",
    "approve": "approve",
    "reject": "approve",
    "post": "post",
    "reverse": "void",
    "simulate": "read",
}


class VoucherAction(_Voucher):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        obj = self.one(ctx, company, ACTION_PERM[action], pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)
        effective = request.data.get("effective_date") if isinstance(request.data, dict) else None

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            if action == "simulate":
                return vouchers.simulate(u, obj), 200
            if action == "submit":
                done = vouchers.submit(u, obj, reason)
            elif action == "approve":
                done = vouchers.decide(u, obj, bool(params.get("approve", True)), reason)
            elif action == "reject":
                done = vouchers.decide(u, obj, False, reason)
            elif action == "post":
                done = vouchers.post(u, obj)
            else:
                day = None
                if effective:
                    try:
                        day = dt.date.fromisoformat(str(effective))
                    except ValueError as exc:
                        raise ValidationFailed("effective_date must be a date", field="effective_date") from exc
                done = vouchers.reverse(u, obj, reason, day)
            return ser.voucher_json(ctx, done), 200

        etag = None if action == "simulate" else _etag(TreasuryVoucher)
        return command(request, ctx, f"{self.perm}.{action}", fn, etag=etag)


class ReceiptList(VoucherList):
    voucher_type, perm = "RC", "treasury.receipt"


class ReceiptDetail(VoucherDetail):
    voucher_type, perm = "RC", "treasury.receipt"


class ReceiptAction(VoucherAction):
    voucher_type, perm = "RC", "treasury.receipt"


class DisbursementList(VoucherList):
    voucher_type, perm = "CE", "treasury.disbursement"


class DisbursementDetail(VoucherDetail):
    voucher_type, perm = "CE", "treasury.disbursement"


class DisbursementAction(VoucherAction):
    voucher_type, perm = "CE", "treasury.disbursement"


RECEIPT_VIEWS = (ReceiptList.as_view(), ReceiptDetail.as_view(), ReceiptAction.as_view())
DISBURSEMENT_VIEWS = (DisbursementList.as_view(), DisbursementDetail.as_view(), DisbursementAction.as_view())


# --------------------------------------------------------------------------- payment batches


class BatchList(TreasuryView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "treasury.payment_batch.read", PaymentBatch.objects.filter(company_id=company))
        return page(request, qs, ser.batch_json, {"status": "status", "number": "number"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            "treasury.payment_batch.create",
            lambda u: (ser.batch_json(batches.create_batch(u, company, body(request))), 201),
            etag=_etag(PaymentBatch),
        )


class BatchDetail(TreasuryView):
    def _one(self, request: Request, action: str, pk: uuid.UUID) -> tuple[TenantContext, PaymentBatch]:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, f"treasury.payment_batch.{action}", PaymentBatch.objects.filter(company_id=company), pk)
        return ctx, obj

    def get(self, request: Request, pk: uuid.UUID) -> Response:
        _ctx, obj = self._one(request, "read", pk)
        return _with_etag(ser.batch_json(obj), obj)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, obj = self._one(request, "update", pk)
        check_if_match(request, obj, required=True)
        return command(
            request,
            ctx,
            "treasury.payment_batch.update",
            lambda u: (ser.batch_json(batches.update_batch(u, obj, body(request))), 200),
            etag=_etag(PaymentBatch),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, obj = self._one(request, "delete", pk)
        check_if_match(request, obj, required=True)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            batches.cancel_batch(u, obj)
            return None, 204

        return command(request, ctx, "treasury.payment_batch.delete", fn)


BATCH_PERM = {
    "submit": "update",
    "approve": "approve",
    "generate-file": "update",
    "mark-sent": "update",
    "execute": "post",
}


def _file_operation(batch: PaymentBatch) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": "treasury_payment_batches_by_generate_file",
        "status": "SUCCEEDED",
        "progress": "100",
        "started_at": None,
        "finished_at": None,
        "result_links": [{"rel": "payment-batch", "href": f"/api/v1/treasury/payment-batches/{batch.id}"}],
        "error": None,
        "file": batches.file_info(batch),
    }


class BatchAction(TreasuryView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(
            ctx, f"treasury.payment_batch.{BATCH_PERM[action]}", PaymentBatch.objects.filter(company_id=company), pk
        )
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            if action == "submit":
                return ser.batch_json(batches.submit_batch(u, obj, reason)), 200
            if action == "approve":
                return ser.batch_json(batches.approve_batch(u, obj, bool(params.get("approve", True)), reason)), 200
            if action == "mark-sent":
                return ser.batch_json(batches.mark_sent(u, obj)), 200
            if action == "generate-file":
                return _file_operation(batches.generate_file(u, obj)), 202
            done, operation = batches.execute_batch(u, obj)
            return {**operation, "file": batches.file_info(done)}, 202

        etag = _etag(PaymentBatch) if action in ("submit", "approve", "mark-sent") else None
        return command(request, ctx, f"treasury.payment_batch.{action}", fn, etag=etag)


# --------------------------------------------------------------------------- statements / reconciliation


class StatementList(TreasuryView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "treasury.statement.read", BankStatement.objects.filter(company_id=company))
        return page(request, qs, ser.statement_json, {"status": "status", "bank_account_id": "bank_account_id"})

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        return command(
            request,
            ctx,
            "treasury.statement.create",
            lambda u: (ser.statement_json(recon.create_statement(u, company, body(request))), 201),
            etag=_etag(BankStatement),
        )


class StatementDetail(TreasuryView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "treasury.statement.read", BankStatement.objects.filter(company_id=company), pk)
        return _with_etag(ser.statement_json(obj), obj)


class StatementImport(TreasuryView):
    parser_classes: ClassVar[list[Any]] = [MultiPartParser, FormParser, JSONParser]

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationFailed("file is required (multipart/form-data)", field="file")
        content = upload.read()

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            created = recon.import_csv(u, company, content)
            return {
                "id": str(uuid.uuid4()),
                "kind": "treasury_statements_import",
                "status": "SUCCEEDED",
                "progress": "100",
                "started_at": None,
                "finished_at": None,
                "result_links": [{"rel": "statement", "href": f"/api/v1/treasury/statements/{s.id}"} for s in created],
                "error": None,
            }, 202

        digest = hashlib.sha256(content).hexdigest()
        return command(request, ctx, "treasury.statement.import", fn, payload={"path": request.path, "sha256": digest})


class StatementLineList(TreasuryView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        qs = scoped(ctx, "treasury.statement_line.read", StatementLine.objects.filter(company_id=company))
        return page(request, qs, ser.statement_line_json, {"statement_id": "statement_id", "status": "state"})


class StatementLineDetail(TreasuryView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "treasury.statement_line.read", StatementLine.objects.filter(company_id=company), pk)
        return _with_etag(ser.statement_line_json(obj), obj)


class StatementLineAction(TreasuryView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        ctx, company = self.ctx_company(request)
        obj = get_scoped(ctx, "treasury.statement_line.update", StatementLine.objects.filter(company_id=company), pk)
        check_if_match(request, obj, required=False)
        reason, params = action_body(request)

        def fn(u: UnitOfWork) -> tuple[Any, int]:
            if action == "match":
                vid = uuid_in(params.get("voucher_id"), "parameters.voucher_id", required=False)
                return ser.statement_line_json(recon.match(u, obj, vid)), 200
            return ser.statement_line_json(recon.unmatch(u, obj, reason)), 200

        return command(request, ctx, f"treasury.statement_line.{action}", fn, etag=_etag(StatementLine))


# --------------------------------------------------------------------------- KPIs (module-registry "tesoreria")


class KpiView(TreasuryView):
    def _kpi_company(self, request: Request) -> tuple[TenantContext, uuid.UUID]:
        raw = request.query_params.get("company_id")
        if raw:
            ctx = self.context()
            cid = uuid_in(raw, "company_id")
            assert cid is not None  # noqa: S101 - required above
            return ctx, ctx.require_company(cid)
        return self.ctx_company(request)

    def get(self, request: Request) -> Response:
        ctx, company = self._kpi_company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        compare = request.query_params.get("compare", "previous") == "previous"
        return Response(kpis.tesoreria_kpis(ctx, company, period, compare))


class KpiPanelView(KpiView):
    def get(self, request: Request, panel: str = "") -> Response:
        if panel != "flujo_caja":
            raise NotFound("panel not found")
        ctx, company = self._kpi_company(request)
        period = request.query_params.get("period_id") or request.query_params.get("period")
        return Response(kpis.cash_flow_panel(ctx, company, period))
