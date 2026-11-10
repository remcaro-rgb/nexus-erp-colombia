"""F-INV-ISSUE API (contracts/openapi/modules/inventory.yaml): ``issue-concepts`` (CRUD; DELETE archives) and
``issues`` (CRUD; DELETE cancels a non-posted issue) + ``:submit`` ``:approve`` ``:post`` ``:reverse`` ``:simulate``,
``issues:export`` (CSV) and ``issues/{id}/pdf``.

Cost fields are confidential: ``total``, line ``unit_cost``/``value`` and the ``:simulate`` preview are only returned
to roles that may read confidential fields (same field policy as UI, export, AI and MCP)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from decimal import Decimal
from typing import Any

from django.http import HttpResponse
from rest_framework.request import Request
from rest_framework.response import Response

from inventory.api.base import InventoryView, body, decimal, money, s, uuid_or_none
from inventory.issues import service as issues
from inventory.issues.models import IssueConcept, StockIssue, StockIssueLine
from kernel import clock, services, tenancy
from kernel.errors import NotFound, ValidationFailed
from kernel.interfaces.documents import DocumentRenderer, RenderedDocument
from kernel.uow import UnitOfWork, run_in_unit_of_work

WATERMARK = "SINTÉTICO — documento interno de inventario (DEV)"


def _only(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(
            f"unknown fields: {', '.join(unknown)}", pointer=f"/{unknown[0]}", reason="additional_property"
        )


def _date(value: Any, field: str, default: dt.date | None = None) -> dt.date | None:
    if value in (None, ""):
        return default
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be YYYY-MM-DD", pointer=f"/{field}", reason="format") from None


def _dims(raw: Any, pointer: str) -> dict[str, uuid.UUID | None]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValidationFailed("dimensions must be an object", pointer=pointer, reason="type")
    slots = {v: k for k, v in issues.DIM_FIELDS.items()}
    out: dict[str, uuid.UUID | None] = {}
    for slot, value in raw.items():
        if slot == "branch_id":  # the branch comes from the warehouse (F-ACC-BRANCH)
            continue
        if slot not in slots:
            raise ValidationFailed(
                f"unknown dimension slot {slot}", pointer=f"{pointer}/{slot}", reason="additional_property"
            )
        out[slots[slot]] = uuid_or_none(value, f"{pointer[1:]}/{slot}")
    return out


def _lines(raw: Any) -> tuple[issues.IssueLineInput, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValidationFailed("lines required", pointer="/lines", reason="required")
    out = []
    for i, ln in enumerate(raw):
        if not isinstance(ln, dict):
            raise ValidationFailed("line must be an object", pointer=f"/lines/{i}", reason="type")
        _only(ln, {"line_no", "item_id", "description", "quantity", "qty_packages", "presentation_id", "lot_id",
                   "location_id", "dimensions", "unit_price", "amount", "tax_code", "account_code"})  # fmt: skip
        if ln.get("unit_price") or ln.get("amount") or ln.get("account_code"):
            raise ValidationFailed(
                "the cost of an issue comes from the valuation layers and the account from the concept",
                pointer=f"/lines/{i}",
                reason="read_only",
            )
        item_id = uuid_or_none(ln.get("item_id"), f"lines/{i}/item_id")
        if item_id is None:
            raise ValidationFailed("item_id required", pointer=f"/lines/{i}/item_id", reason="required")
        q = ln.get("quantity")
        qvalue = q.get("value") if isinstance(q, dict) else q
        out.append(
            issues.IssueLineInput(
                item_id=item_id,
                quantity=decimal(qvalue, f"lines/{i}/quantity", required=False),
                qty_packages=decimal(ln.get("qty_packages"), f"lines/{i}/qty_packages", required=False),
                presentation_id=uuid_or_none(ln.get("presentation_id"), f"lines/{i}/presentation_id"),
                location_id=uuid_or_none(ln.get("location_id"), f"lines/{i}/location_id"),
                lot_id=uuid_or_none(ln.get("lot_id"), f"lines/{i}/lot_id"),
                description=str(ln.get("description") or ""),
                dimensions=_dims(ln.get("dimensions"), f"/lines/{i}/dimensions"),
            )
        )
    return tuple(out)


def _currency(data: dict[str, Any]) -> None:
    if data.get("currency") not in (None, "COP"):
        raise ValidationFailed(
            "stock issues are valued in COP (functional currency)", pointer="/currency", reason="enum"
        )


# =========================================================================== concepts


def concept_dict(c: IssueConcept) -> dict[str, Any]:
    return {
        "id": s(c.id),
        "tenant_id": s(c.tenant_id),
        "company_id": s(c.company_id),
        "code": c.code,
        "name": c.name,
        "status": c.status,
        "kind": c.kind,
        "account_role": c.account_role,
        "requires_dimension": c.requires_dimension,
        "required_dims": c.required_dims,
        "requires_reason": c.requires_reason,
        "reason_codes": c.reason_codes,
        "requires_approval": c.requires_approval,
        "approval_threshold_value": money(c.approval_threshold_value),
        "version": c.version,
        "audit": {"at": c.updated_at.isoformat(), "by": s(c.updated_by or c.created_by)},
    }


CONCEPT_FIELDS = {
    "code", "name", "account_role", "requires_dimension", "requires_reason", "requires_approval", "kind",
    "required_dims", "reason_codes", "approval_threshold_value",
}  # fmt: skip


class ConceptListView(InventoryView):
    perm, resource_type = "inventory.issue_concept", "inventory.issue_concept"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return concept_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", IssueConcept.objects.all(), company)
        return self.paginate(
            request, qs, search_fields=("code", "name"), filter_fields={"status": "status", "kind": "kind"},
            sort_fields=("code", "name"),
        )  # fmt: skip

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, CONCEPT_FIELDS)
        return self.command(
            request,
            "inventory.issue_concept.create",
            lambda u: (self.serialize(issues.create_concept(u, company, data)), 201),
        )


class ConceptDetailView(ConceptListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        c = self.one("read", IssueConcept.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(c), c)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        c = self.one("update", IssueConcept.objects.all(), company, pk)
        self.if_match(request, c)
        data = body(request)
        _only(data, CONCEPT_FIELDS)
        return self.command(
            request,
            "inventory.issue_concept.update",
            lambda u: (self.serialize(issues.update_concept(u, c, data)), 200),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        c = self.one("delete", IssueConcept.objects.all(), company, pk)

        def run(u: UnitOfWork) -> tuple[Any, int]:
            issues.archive_concept(u, c)
            return None, 204

        return self.command(request, "inventory.issue_concept.delete", run)


# =========================================================================== issues


def issue_dict(i: StockIssue, with_cost: bool) -> dict[str, Any]:
    lines = list(StockIssueLine.objects.filter(issue_id=i.id).select_related("item__base_uom").order_by("line_no"))
    concept = IssueConcept.objects.filter(id=i.concept_id).values_list("code", flat=True).first()

    def line(ln: StockIssueLine) -> dict[str, Any]:
        out: dict[str, Any] = {
            "line_no": ln.line_no,
            "item_id": s(ln.item_id),
            "description": ln.description or ln.item.name,
            "quantity": {"value": format(ln.quantity, "f"), "uom": ln.uom},
            "qty_packages": s(ln.qty_packages),
            "presentation_id": s(ln.presentation_id),
            "lot_id": s(ln.lot_id),
            "location_id": s(ln.location_id),
            "dimensions": {col: s(getattr(ln, col)) for col in issues.DIM_FIELDS.values()},
        }
        if with_cost:
            out["unit_price"] = money(ln.unit_cost) if ln.unit_cost is not None else None
            out["amount"] = money(ln.value)
        return out

    return {
        "id": s(i.id),
        "tenant_id": s(i.tenant_id),
        "company_id": s(i.company_id),
        "number": i.number,
        "document_date": i.document_date.isoformat(),
        "posting_date": i.posting_date.isoformat() if i.posting_date else None,
        "status": i.status,
        "currency": "COP",
        "total": money(i.total),
        "notes": i.notes,
        "source": {"type": issues.SOURCE_TYPE, "id": s(i.id), "number": i.number or None},
        "concept_id": s(i.concept_id),
        "concept_code": concept,
        "warehouse_id": s(i.warehouse_id),
        "branch_id": s(i.branch_id),
        "reason": i.reason,
        "dimensions": {**{col: s(getattr(i, col)) for col in issues.DIM_FIELDS.values()}, "branch_id": s(i.branch_id)},
        "lines": [line(ln) for ln in lines],
        "approval_id": s(i.approval_id),
        "requested_by": s(i.requested_by),
        "journal_entry_id": s(i.journal_entry_id),
        "reversal_entry_id": s(i.reversal_entry_id),
        "reversal_reason": i.reversal_reason or None,
        "posted_at": i.posted_at.isoformat() if i.posted_at else None,
        "reversed_at": i.reversed_at.isoformat() if i.reversed_at else None,
        "version": i.version,
        "audit": {"at": i.updated_at.isoformat(), "by": s(i.updated_by or i.created_by)},
    }


ISSUE_FIELDS = {
    "document_date", "posting_date", "currency", "notes", "concept_id", "warehouse_id", "reason", "dimensions", "lines",
}  # fmt: skip


def _input(data: dict[str, Any]) -> issues.IssueInput:
    concept_id = uuid_or_none(data.get("concept_id"), "concept_id")
    if concept_id is None:
        raise ValidationFailed("concept_id is required", pointer="/concept_id", reason="required")
    warehouse_id = uuid_or_none(data.get("warehouse_id"), "warehouse_id")
    if warehouse_id is None:
        raise ValidationFailed("warehouse_id is required", pointer="/warehouse_id", reason="required")
    _currency(data)
    return issues.IssueInput(
        concept_id=concept_id,
        warehouse_id=warehouse_id,
        document_date=_date(data.get("document_date"), "document_date", clock.business_date()) or clock.business_date(),
        posting_date=_date(data.get("posting_date"), "posting_date"),
        reason=str(data["reason"]) if data.get("reason") is not None else None,
        notes=str(data.get("notes") or ""),
        dimensions=_dims(data.get("dimensions"), "/dimensions"),
        lines=_lines(data.get("lines")),
    )


class IssueView(InventoryView):
    perm, resource_type = "inventory.issue", "inventory.issue"

    def can_see_cost(self) -> bool:
        readable, _w, masks = self.field_view()
        return "total" in readable and "total" not in masks

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return issue_dict(obj, self.can_see_cost())


class IssueListView(IssueView):
    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", StockIssue.objects.all(), company)
        return self.paginate(
            request,
            qs,
            search_fields=("number", "reason"),
            filter_fields={"status": "status", "warehouse_id": "warehouse_id", "concept_id": "concept_id"},
            sort_fields=("document_date", "number"),
        )

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = body(request)
        _only(data, ISSUE_FIELDS)
        inp = _input(data)
        with tenancy.scope(self.ctx):
            # the warehouse must be inside the caller's warehouse/branch scope (row policy, not only RLS)
            from inventory.models import Warehouse

            wh_ok = self.policy_scope("inventory.warehouse.read", Warehouse.objects.filter(id=inp.warehouse_id))
            if not wh_ok:
                raise NotFound("warehouse not found")
        return self.command(
            request, "inventory.issue.create", lambda u: (self.serialize(issues.create(u, company, inp)), 201)
        )

    def policy_scope(self, action: str, qs: Any) -> bool:
        from inventory.api.base import policy

        return bool(policy().scope_queryset(self.ctx, action, qs).exists())


class IssueDetailView(IssueView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:
        i = self.one("read", StockIssue.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(i), i)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        i = self.one("update", StockIssue.objects.all(), company, pk)
        self.if_match(request, i)
        data = body(request)
        _only(data, ISSUE_FIELDS)
        if not data:
            raise ValidationFailed("nothing to update", pointer="/", reason="min_properties")
        _currency(data)
        changes: dict[str, Any] = {}
        for key in ("concept_id", "warehouse_id"):
            if key in data:
                value = uuid_or_none(data[key], key)
                if value is None:
                    raise ValidationFailed(f"{key} is required", pointer=f"/{key}", reason="required")
                changes[key] = value
        if "document_date" in data:
            changes["document_date"] = _date(data["document_date"], "document_date", clock.business_date())
        if "posting_date" in data:
            changes["posting_date"] = _date(data["posting_date"], "posting_date")
        for key in ("notes", "reason"):
            if key in data:
                changes[key] = "" if data[key] is None and key == "notes" else data[key]
        if "dimensions" in data:
            changes["dimensions"] = _dims(data["dimensions"], "/dimensions")
        lines = _lines(data["lines"]) if "lines" in data else None
        return self.command(
            request, "inventory.issue.update", lambda u: (self.serialize(issues.update(u, i, changes, lines)), 200)
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        company = self.company(request)
        i = self.one("delete", StockIssue.objects.all(), company, pk)

        def run(u: UnitOfWork) -> tuple[Any, int]:
            issues.cancel(u, i)
            return None, 204

        return self.command(request, "inventory.issue.delete", run)


ACTIONS = {"submit": "update", "approve": "approve", "post": "post", "reverse": "void", "simulate": "read"}


class IssueActionView(IssueView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:
        if action not in ACTIONS:
            raise NotFound("unknown action")
        company = self.company(request)
        i = self.one(ACTIONS[action], StockIssue.objects.all(), company, pk)
        if request.headers.get("If-Match"):
            self.if_match(request, i)
        data = body(request)
        _only(data, {"reason", "comment", "effective_date", "parameters"})
        params = data.get("parameters") or {}
        if not isinstance(params, dict):
            raise ValidationFailed("parameters must be an object", pointer="/parameters", reason="type")
        reason = str(data.get("reason") or "")
        day = _date(data.get("effective_date"), "effective_date")

        def run(u: UnitOfWork) -> tuple[Any, int]:
            if action == "submit":
                return self.serialize(issues.submit(u, i)), 200
            if action == "approve":
                decision = params.get("decision", "approve")
                if decision not in ("approve", "reject"):
                    raise ValidationFailed(
                        "decision is approve or reject", pointer="/parameters/decision", reason="enum"
                    )
                if decision == "reject" and not reason.strip():
                    raise ValidationFailed("a rejection needs a reason", code="reason_required", pointer="/reason")
                return self.serialize(issues.approve(u, i, decision == "approve", reason)), 200
            if action == "post":
                return self.serialize(issues.post(u, i, day)), 200
            if action == "reverse":
                return self.serialize(issues.reverse(u, i, reason, day)), 200
            preview = issues.simulate(u, i, day)
            result = {
                "status": "simulated",
                "resource": {"type": issues.SOURCE_TYPE, "id": s(i.id), "number": i.number or None},
                "data": {
                    "total": money(preview.total),
                    "lines": preview.lines,
                    "journal": preview.journal,
                },
                "warnings": [],
            }
            return self.serialize(result), 200

        return self.command(request, f"inventory.issue.{action}", run)


class IssueExportView(IssueView):
    """``issues:export`` — CSV of the scoped list; the value column only for roles that see confidential fields."""

    def get(self, request: Request) -> HttpResponse:
        company = self.company(request)
        self.allow("export")
        qs = self.scoped("read", StockIssue.objects.all(), company).order_by("document_date", "number")
        status = request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        with_cost = self.can_see_cost()
        out = io.StringIO()
        w = csv.writer(out)
        header = ["numero", "fecha", "estado", "concepto", "bodega_id", "centro_costo_id", "motivo"]
        w.writerow([*header, "valor_cop"] if with_cost else header)
        codes = dict(IssueConcept.objects.filter(company_id=company).values_list("id", "code"))
        for i in qs[:50000]:
            row = [i.number, i.document_date.isoformat(), i.status, codes.get(i.concept_id, ""), str(i.warehouse_id),
                   str(i.cost_center_id or ""), i.reason or ""]  # fmt: skip
            w.writerow([*row, format(i.total or Decimal(0), "f")] if with_cost else row)
        resp = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="salidas-inventario.csv"'
        return resp


class IssuePdfView(IssueView):
    def get(self, request: Request, pk: uuid.UUID) -> HttpResponse:
        company = self.company(request)
        i = self.one("export", StockIssue.objects.all(), company, pk)
        data = self.serialize(i)

        def render(u: UnitOfWork) -> Any:
            doc = services.get(DocumentRenderer).render("inventory.stock_issue", data, watermark=WATERMARK)
            from inventory.common import audit

            audit(u, "print", "inventory.issue", i)
            return doc

        doc = run_in_unit_of_work(render, self.ctx)
        if not isinstance(doc, RenderedDocument):  # pragma: no cover - no idempotency key → never a replay
            raise ValidationFailed("unexpected render result")
        resp = HttpResponse(doc.content, content_type=doc.media_type)
        resp["Content-Disposition"] = f'attachment; filename="salida-{i.number or i.id}.pdf"'
        return resp
