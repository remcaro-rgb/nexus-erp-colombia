"""Ledger API: dimension rules/settings, entries (simulate/post/reverse), periods, accounts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from rest_framework.request import Request
from rest_framework.response import Response

from kernel import ids, services, tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.common import DimensionValues, DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.uow import UnitOfWork
from ledger import periods as period_svc
from ledger import rules as rule_matrix
from ledger.api import LedgerAPIView, command, require, respond, scoped
from ledger.dimensions import service as dims
from ledger.dimensions.views import _body
from ledger.models import DIMENSIONS, Account, DimensionSettings, JournalEntry, JournalLine, Period
from ledger.posting import LedgerPostingService

MANUAL_SOURCE = "ledger.manual"


# --------------------------------------------------------------------------- rules / settings


def _ruleset_body(rs: Any) -> dict[str, Any]:
    return {
        "id": str(rs.id),
        "tenant_id": str(rs.tenant_id),
        "company_id": str(rs.company_id),
        "rules": [
            {
                "dimension": r.dimension,
                "scope_type": r.scope_type,
                "scope_ref": r.scope_ref,
                "mode": r.mode,
                "priority": r.priority,
            }
            for r in rs.rules.order_by("position")
        ],
        "version": rs.version,
    }


class DimensionRulesView(LedgerAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.dimension_rule.read")
        with tenancy.scope(ctx):
            rs = rule_matrix.rule_set(ctx, company)
            body = _ruleset_body(rs)
        return respond(body, etag=rs.etag)

    def put(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.dimension_rule.update")
        data = _body(request)
        if set(data) - {"rules"}:
            raise ValidationFailed("unknown fields", pointer="/", reason="additional_property")
        rules = rule_matrix.validate_rules(data.get("rules"))

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            rs = rule_matrix.replace_rules(uow, company, rules, request.headers.get("If-Match"))
            return _ruleset_body(rs), 200, rs.etag

        return command(request, ctx, "ledger.dimension_rules.replace", fn)


class DimensionSettingsView(LedgerAPIView):
    def _body(self, ctx: Any, company: uuid.UUID, dimension: str) -> tuple[dict[str, Any], str | None]:
        row = DimensionSettings.objects.filter(tenant_id=ctx.tenant_id, company_id=company, dimension=dimension).first()
        return (
            {
                "id": str(row.id) if row else None,
                "tenant_id": str(ctx.tenant_id),
                "company_id": str(company),
                "dimension": dimension,
                "max_depth": row.max_depth if row else dims.max_depth(ctx, company, dimension),
                "version": row.version if row else 0,
            },
            row.etag if row else None,
        )

    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.dimension_rule.read")
        dimension = request.query_params.get("dimension", "cost_center")
        if dimension not in DIMENSIONS:
            raise ValidationFailed("unknown dimension", pointer="/query/dimension", reason="filter_invalid")
        with tenancy.scope(ctx):
            body, etag = self._body(ctx, company, dimension)
        return respond(body, etag=etag)

    def put(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.dimension_rule.update")
        data = _body(request)
        dimension = data.get("dimension")
        if dimension not in DIMENSIONS:
            raise ValidationFailed("unknown dimension", pointer="/dimension", reason="enum")
        value = data.get("max_depth")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationFailed("max_depth must be an integer", pointer="/max_depth", reason="type")

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            dims.set_max_depth(uow, company, dimension, value)
            body, etag = self._body(uow.ctx, company, dimension)
            return body, 200, etag

        return command(request, ctx, "ledger.dimension_settings.replace", fn)


# --------------------------------------------------------------------------- entries


def _decimal(value: Any, pointer: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValidationFailed("amounts are decimal strings", pointer=pointer, reason="type")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationFailed("invalid amount", pointer=pointer, reason="format") from None


def _opt_uuid(value: Any, pointer: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationFailed("invalid id", pointer=pointer, reason="format") from None


def parse_entry(company: uuid.UUID, data: dict[str, Any], source_id: uuid.UUID | None = None) -> PostingRequest:
    """Ad-hoc entry body → :class:`PostingRequest` (manual entries reference accounts by code)."""
    try:
        posting_date = dt.date.fromisoformat(str(data.get("posting_date")))
    except ValueError:
        raise ValidationFailed("posting_date required (YYYY-MM-DD)", pointer="/posting_date", reason="format") from None
    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValidationFailed("lines required", pointer="/lines", reason="required")
    if len(raw_lines) > 1000:
        raise ValidationFailed("at most 1000 lines", pointer="/lines", reason="bulk_limit_exceeded")
    lines = []
    for i, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise ValidationFailed("line must be an object", pointer=f"/lines/{i}", reason="type")
        debit = _decimal(raw.get("debit") or "0", f"/lines/{i}/debit")
        credit = _decimal(raw.get("credit") or "0", f"/lines/{i}/credit")
        if (debit > 0) == (credit > 0):
            raise ValidationFailed(
                "exactly one of debit/credit must be positive", pointer=f"/lines/{i}", reason="one_side"
            )
        role = raw.get("account_role") or (f"account:{raw['account']}" if raw.get("account") else None)
        if not role:
            raise ValidationFailed("account required", pointer=f"/lines/{i}/account", reason="required")
        slots = raw.get("dimensions") or {}
        lines.append(
            PostingLine(
                account_role=str(role),
                side="DEBIT" if debit > 0 else "CREDIT",
                amount=debit if debit > 0 else credit,
                currency=str(raw.get("currency") or "COP"),
                fx_rate=_decimal(raw.get("fx_rate") or "1", f"/lines/{i}/fx_rate"),
                memo=str(raw.get("memo") or "")[:500],
                dimensions=DimensionValues(
                    **{s: _opt_uuid(slots.get(s), f"/lines/{i}/dimensions/{s}") for s in DimensionValues.__slots__}
                ),
            )
        )
    return PostingRequest(
        company_id=company,
        posting_date=posting_date,
        source=DocumentRef(str(data.get("source_type") or MANUAL_SOURCE), source_id or ids.new_id()),
        purpose=str(data.get("purpose") or "manual")[:120],
        lines=tuple(lines),
        journal_code=str(data.get("journal_code") or "GENERAL"),
    )


def entry_body(e: JournalEntry, with_lines: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(e.id),
        "company_id": str(e.company_id),
        "number": e.number,
        "book": e.book.code,
        "journal": e.journal.code,
        "period": e.period.code,
        "posting_date": e.posting_date.isoformat(),
        "status": e.status,
        "source": {"type": e.source_type, "id": str(e.source_id), "number": e.source_number or None},
        "purpose": e.purpose,
        "reversal_of": str(e.reversal_of_id) if e.reversal_of_id else None,
        "reversed_by": str(e.reversed_by_id) if e.reversed_by_id else None,
        "memo": e.memo,
        "total_debit": {"amount": f"{e.total_debit:.4f}", "currency": "COP"},
        "total_credit": {"amount": f"{e.total_credit:.4f}", "currency": "COP"},
    }
    if with_lines:
        body["lines"] = [
            {
                "line_no": ln.line_no,
                "account": ln.account.code,
                "debit": {"amount": f"{ln.debit:.4f}", "currency": "COP"},
                "credit": {"amount": f"{ln.credit:.4f}", "currency": "COP"},
                "amount_currency": {"amount": f"{ln.amount_currency:.4f}", "currency": ln.currency},
                "dimensions": {s: (str(getattr(ln, s)) if getattr(ln, s) else None) for s in DimensionValues.__slots__},
                "is_rounding": ln.is_rounding,
                "memo": ln.memo,
            }
            for ln in JournalLine.objects.filter(entry=e).select_related("account").order_by("line_no")
        ]
    return body


class JournalEntrySimulateView(LedgerAPIView):
    """``POST /journal-entries:simulate`` — same validation as posting, nothing persisted."""

    def post(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.entry.read")
        body = _body(request)
        params = body.get("parameters") if isinstance(body.get("parameters"), dict) else body
        posting = services.get(PostingService)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            req = parse_entry(company, params)
            if isinstance(posting, LedgerPostingService):
                plan = posting.plan(uow, req)
                entries = [plan.to_json()]
            else:
                result = posting.simulate(uow, req)
                entries = [{"total_debit": {"amount": f"{result.total_debit_cop:.4f}", "currency": "COP"}}]
            return {"persisted": False, "documents": [], "journal_entries": entries, "stock_moves": []}, 200, None

        return command(request, ctx, "ledger.journal_entries.simulate", fn)


class JournalEntryListView(LedgerAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        p = request.query_params
        with tenancy.scope(ctx):
            qs = scoped(
                ctx, "ledger.entry.read", JournalEntry.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
            )
            if p.get("book"):
                qs = qs.filter(book__code=p["book"])
            if p.get("status"):
                qs = qs.filter(status=p["status"])
            qs = qs.select_related("book", "journal", "period").order_by("-posting_date", "-created_at")
            limit = min(int(p.get("limit") or 50), 200)
            items = [entry_body(e, with_lines=False) for e in qs[:limit]]
        return Response({"items": items, "next_cursor": None})

    def post(self, request: Request) -> Response:
        """Create and post a manual entry (``ledger.entry.post``) in one UoW."""
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.entry.post")
        data = _body(request)
        posting = services.get(PostingService)

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            req = parse_entry(company, {**data, "source_type": MANUAL_SOURCE})
            result = posting.post(uow, req)
            e = JournalEntry.objects.select_related("book", "journal", "period").get(id=result.entry_id)
            return entry_body(e), 201, None

        return command(request, ctx, "ledger.entries.post", fn)


class JournalEntryDetailView(LedgerAPIView):
    def get(self, request: Request, entry_id: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            qs = scoped(
                ctx, "ledger.entry.read", JournalEntry.objects.filter(tenant_id=ctx.tenant_id, company_id=company)
            )
            e = qs.select_related("book", "journal", "period").filter(id=entry_id).first()
            if e is None:
                raise NotFound("journal entry not found")
            body = entry_body(e)
        return Response(body)


class JournalEntryReverseView(LedgerAPIView):
    def post(self, request: Request, entry_id: uuid.UUID) -> Response:
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.entry.void")
        data = _body(request, optional=True)
        reason = str(data.get("reason") or "")
        try:
            day = dt.date.fromisoformat(str(data.get("effective_date"))) if data.get("effective_date") else None
        except ValueError:
            raise ValidationFailed("invalid effective_date", pointer="/effective_date", reason="format") from None

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            original = JournalEntry.objects.filter(tenant_id=ctx.tenant_id, company_id=company, id=entry_id).first()
            if original is None:
                raise NotFound("journal entry not found")
            result = services.get(PostingService).reverse(uow, entry_id, day or original.posting_date, reason)
            e = JournalEntry.objects.select_related("book", "journal", "period").get(id=result.entry_id)
            return entry_body(e), 200, None

        return command(request, ctx, "ledger.entries.reverse", fn)


# --------------------------------------------------------------------------- periods / accounts


def period_body(p: Period) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "company_id": str(p.company_id),
        "code": p.code,
        "fiscal_year": p.fiscal_year,
        "number": p.number,
        "start": p.start.isoformat(),
        "end": p.end.isoformat(),
        "status": p.status,
        "version": p.version,
    }


class PeriodListView(LedgerAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            qs = scoped(ctx, "ledger.period.read", Period.objects.filter(tenant_id=ctx.tenant_id, company_id=company))
            items = [period_body(p) for p in qs.order_by("start", "number")]
        return Response({"items": items, "next_cursor": None})


class PeriodActionView(LedgerAPIView):
    TARGETS = {"soft-close": "SOFT_CLOSED", "close": "CLOSED"}

    def post(self, request: Request, period_id: uuid.UUID, action: str) -> Response:
        if action not in self.TARGETS:
            raise NotFound("unknown action")
        ctx, company = self.ctx_company(request)
        require(ctx, "ledger.period.update")

        def fn(uow: UnitOfWork) -> tuple[Any, int, str | None]:
            p = Period.objects.filter(tenant_id=ctx.tenant_id, company_id=company, id=period_id).first()
            if p is None:
                raise NotFound("period not found")
            p = period_svc.change_status(uow, p, self.TARGETS[action])
            return period_body(p), 200, p.etag

        return command(request, ctx, f"ledger.periods.{action}", fn)


class AccountListView(LedgerAPIView):
    def get(self, request: Request) -> Response:
        ctx, company = self.ctx_company(request)
        with tenancy.scope(ctx):
            qs = scoped(ctx, "ledger.account.read", Account.objects.filter(tenant_id=ctx.tenant_id, company_id=company))
            items = [
                {
                    "id": str(a.id),
                    "code": a.code,
                    "name": a.name,
                    "type": a.type,
                    "nature": a.nature,
                    "postable": a.postable,
                    "is_control": a.is_control,
                    "requires_dimensions": a.requires_dimensions,
                    "active": a.active,
                }
                for a in qs.order_by("code")
            ]
        return Response({"items": items, "next_cursor": None})
