"""Transformation API (contracts/openapi/modules/inventory.yaml § transformations, portioning-templates, repack-rules).

* ``GET/POST /transformations`` · ``GET /transformations:export`` (CSV) · ``GET/PATCH/DELETE /transformations/{id}`` ·
  ``POST /transformations/{id}:reserve|:submit|:approve|:reject|:cancel|:post|:reverse|:simulate``.
* ``GET/POST /portioning-templates`` · ``GET/PATCH/DELETE /portioning-templates/{id}`` · ``GET /repack-rules[/{id}]``.
* Permission per action (``inventory.transformation.<verb>``); MANUAL documents additionally need
  ``inventory.transformation_manual.create`` to be created, edited or posted (F-INV-XFORM security). Cost fields are
  ``confidential``: hidden at the top level by the field policy and stripped inside ``lines`` / ``journal`` too.
"""

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

from inventory.api.base import InventoryView, body, decimal, money, require, s, uuid_or_none
from inventory.transformations import service as xf
from inventory.transformations.models import (
    PortioningTemplate,
    PortioningTemplateOutput,
    TransformationLine,
    TransformationOrder,
)
from inventory.transformations.registry import LINE_COST_KEYS, register_fields
from kernel import clock
from kernel.errors import NotFound, ValidationFailed

register_fields()

MANUAL_PERMISSION = "inventory.transformation_manual.create"
ORDER_FIELDS = {
    "document_date",
    "posting_date",
    "currency",
    "notes",
    "reason",
    "transformation_type",
    "template_id",
    "bom_id",
    "planned_qty",
    "warehouse_id",
    "additional_costs",
    "tolerance_percent",
    "lines",
}
LINE_FIELDS = {
    "line_no",
    "item_id",
    "description",
    "quantity",
    "qty_packages",
    "unit_price",
    "amount",
    "tax_code",
    "account_code",
    "dimensions",
    "role",
    "presentation_id",
    "lot_id",
    "lot_code",
    "location_id",
    "cost_share",
    "actual_weight",
    "is_main",
    "is_byproduct",
    "abnormal",
}


def _only(data: dict[str, Any], allowed: set[str], ptr: str = "") -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationFailed(
            f"unknown fields: {', '.join(unknown)}", pointer=f"{ptr}/{unknown[0]}", reason="additional_property"
        )


def _date(value: Any, field: str, default: dt.date | None = None) -> dt.date | None:
    if value in (None, ""):
        return default
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationFailed(f"{field} must be YYYY-MM-DD", pointer=f"/{field}", reason="format") from None


def _num(value: Any, field: str) -> Decimal | None:
    raw = value.get("value", value.get("amount")) if isinstance(value, dict) else value
    return decimal(raw, field, required=False)


def parse_lines(raw_lines: Any) -> tuple[xf.LineInput, ...]:
    if raw_lines is None:
        return ()
    if not isinstance(raw_lines, list):
        raise ValidationFailed("lines must be an array", pointer="/lines", reason="type")
    out: list[xf.LineInput] = []
    for i, raw in enumerate(raw_lines):
        ptr = f"/lines/{i}"
        if not isinstance(raw, dict):
            raise ValidationFailed("line must be an object", pointer=ptr, reason="type")
        _only(raw, LINE_FIELDS, ptr)
        out.append(
            xf.LineInput(
                role=str(raw.get("role") or ""),
                item_id=uuid_or_none(raw.get("item_id"), f"{ptr}/item_id"),
                quantity=_num(raw.get("quantity"), f"{ptr}/quantity"),
                qty_packages=_num(raw.get("qty_packages"), f"{ptr}/qty_packages"),
                presentation_id=uuid_or_none(raw.get("presentation_id"), f"{ptr}/presentation_id"),
                location_id=uuid_or_none(raw.get("location_id"), f"{ptr}/location_id"),
                lot_id=uuid_or_none(raw.get("lot_id"), f"{ptr}/lot_id"),
                lot_code=str(raw.get("lot_code") or ""),
                actual_weight=_num(raw.get("actual_weight"), f"{ptr}/actual_weight"),
                cost_share=_num(raw.get("cost_share"), f"{ptr}/cost_share"),
                is_main=bool(raw.get("is_main")),
                is_byproduct=bool(raw.get("is_byproduct")),
                abnormal=bool(raw.get("abnormal")),
            )
        )
    return tuple(out)


def parse_order(data: dict[str, Any], base: TransformationOrder | None = None) -> xf.OrderInput:
    _only(data, ORDER_FIELDS)
    if data.get("currency") not in (None, "", "COP"):
        raise ValidationFailed("transformations are valued in COP (functional currency)", pointer="/currency")

    def pick(key: str, current: Any) -> Any:
        return data.get(key, current)

    kind = str(pick("transformation_type", base.transformation_type if base else "") or "")
    wh = uuid_or_none(pick("warehouse_id", base.warehouse_id if base else None), "warehouse_id")
    if wh is None:
        raise ValidationFailed("warehouse_id required", pointer="/warehouse_id", reason="required")
    return xf.OrderInput(
        transformation_type=kind,
        warehouse_id=wh,
        document_date=_date(pick("document_date", None), "document_date")
        or (base.document_date if base else clock.business_date()),
        lines=parse_lines(data.get("lines")),
        template_id=uuid_or_none(pick("template_id", base.template_id if base else None), "template_id"),
        bom_id=uuid_or_none(pick("bom_id", base.bom_id if base else None), "bom_id"),
        planned_qty=_num(pick("planned_qty", s(base.planned_qty) if base else None), "planned_qty"),
        additional_costs=_num(pick("additional_costs", s(base.additional_costs) if base else None), "additional_costs")
        or Decimal(0),
        tolerance_percent=decimal(
            pick("tolerance_percent", s(base.tolerance_percent) if base else None), "tolerance_percent", required=False
        ),
        notes=str(pick("notes", base.notes if base else "") or ""),
        reason=str(pick("reason", base.reason if base else "") or ""),
    )


def line_dict(ln: TransformationLine) -> dict[str, Any]:
    uom = ln.item.base_uom.code if ln.item is not None else ""
    return {
        "line_no": ln.line_no,
        "role": ln.role,
        "item_id": s(ln.item_id),
        "item_code": ln.item.code if ln.item is not None else None,
        "quantity": {"value": s(ln.quantity), "uom": uom},
        "qty_packages": s(ln.qty_packages),
        "presentation_id": s(ln.presentation_id),
        "location_id": s(ln.location_id),
        "lot_id": s(ln.lot_id),
        "lot_code": ln.lot_code or None,
        "actual_weight": {"value": s(ln.actual_weight), "uom": uom} if ln.actual_weight is not None else None,
        "cost_share": s(ln.cost_share),
        "is_main": ln.is_main,
        "is_byproduct": ln.is_byproduct,
        "abnormal": ln.abnormal,
        "unit_cost": s(ln.unit_cost),
        "value": money(ln.value),
    }


def order_dict(o: TransformationOrder) -> dict[str, Any]:
    lines = list(TransformationLine.objects.filter(order_id=o.id).select_related("item__base_uom").order_by("line_no"))
    return {
        "id": s(o.id),
        "tenant_id": s(o.tenant_id),
        "company_id": s(o.company_id),
        "number": o.number,
        "transformation_type": o.transformation_type,
        "status": o.status,
        "warehouse_id": s(o.warehouse_id),
        "template_id": s(o.template_id),
        "bom_id": s(o.bom_id),
        "planned_qty": s(o.planned_qty),
        "document_date": o.document_date.isoformat(),
        "posting_date": o.posting_date.isoformat() if o.posting_date else None,
        "currency": "COP",
        "notes": o.notes,
        "reason": o.reason,
        "source": {"type": xf.SOURCE_TYPE, "id": s(o.id), "number": o.number},
        "inputs_reserved": o.inputs_reserved,
        "tolerance_percent": s(o.tolerance_percent),
        "approval_required": o.approval_required,
        "approval_reasons": o.approval_reasons or None,
        "approval_note": o.approval_note or None,
        "approved_by": s(o.approved_by),
        "additional_costs": money(o.additional_costs),
        "value_in": money(o.value_in),
        "value_out": money(o.value_out),
        "waste_expensed": money(o.waste_expensed),
        "total": money(o.value_out),
        "journal_entry_id": s(o.journal_entry_id),
        "reversal_entry_id": s(o.reversal_entry_id),
        "lines": [line_dict(ln) for ln in lines],
        "version": o.version,
    }


def _strip_costs(out: dict[str, Any], cost_key: str) -> dict[str, Any]:
    if cost_key in out:
        return out
    for key in ("lines", "journal"):
        if isinstance(out.get(key), list):
            out[key] = [
                {k: v for k, v in row.items() if k not in LINE_COST_KEYS and k != "amount"}
                for row in out[key]
                if isinstance(row, dict)
            ]
    if "journal" in out:
        out.pop("journal")
    return out


def plan_dict(o: TransformationOrder, p: xf.Plan) -> dict[str, Any]:
    lines = list(TransformationLine.objects.filter(order_id=o.id).select_related("item__base_uom").order_by("line_no"))

    def value_of(ln: TransformationLine) -> Decimal | None:
        if ln.role == TransformationLine.Role.INPUT:
            return p.input_values.get(ln.line_no)
        if ln.role == TransformationLine.Role.OUTPUT:
            return p.output_values.get(ln.line_no)
        return p.waste_values.get(ln.line_no, Decimal(0))

    return {
        "id": s(o.id),
        "balanced": p.balanced,
        "approval_required": p.approval_required,
        "approval_reasons": p.approval_reasons,
        "yield_checks": [
            {
                "expected": s(c.expected),
                "actual": s(c.actual),
                "tolerance": s(c.tolerance),
                "within": c.within,
            }
            for c in p.yield_checks
        ],
        "allocation_basis": p.allocation_basis or None,
        "qty_in": s(p.qty_in),
        "qty_out": s(p.qty_out),
        "abnormal_waste_qty": s(p.abnormal_waste_qty),
        "value_in": money(p.value_in),
        "additional_costs": money(p.additional_costs),
        "waste_expensed": money(p.waste_expensed),
        "value_out": money(p.value_out),
        "lines": [
            {"line_no": ln.line_no, "role": ln.role, "item_id": s(ln.item_id), "value": money(value_of(ln))}
            for ln in lines
        ],
        "journal": [{"account_role": j.account_role, "side": j.side, "amount": money(j.amount)} for j in p.journal],
    }


class TransformationListView(InventoryView):
    perm, resource_type = "inventory.transformation", "inventory.transformation"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return order_dict(obj)

    def serialize(self, obj: Any, fields: Any = None) -> dict[str, Any]:
        return _strip_costs(super().serialize(obj, fields), "total")

    def manual_guard(self, kind: str) -> None:
        if kind == TransformationOrder.Kind.MANUAL:
            require(self.ctx, MANUAL_PERMISSION)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", TransformationOrder.objects.all(), company)
        return self.paginate(
            request,
            qs,
            search_fields=("number",),
            filter_fields={
                "status": "status",
                "transformation_type": "transformation_type",
                "warehouse_id": "warehouse_id",
                "template_id": "template_id",
                "bom_id": "bom_id",
            },
            sort_fields=("document_date", "number"),
        )

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = parse_order(body(request))
        self.manual_guard(data.transformation_type)
        return self.command(
            request, "inventory.transformation.create", lambda u: (self.serialize(xf.create(u, company, data)), 201)
        )


class TransformationExportView(TransformationListView):
    COLUMNS = ("number", "transformation_type", "status", "document_date", "warehouse_id", "value_in", "value_out")

    def get(self, request: Request) -> Any:
        company = self.company(request)
        qs = self.scoped("export", TransformationOrder.objects.all(), company).order_by("document_date", "number")
        readable, _w, _m = self.field_view()
        cols = [c for c in self.COLUMNS if c in readable]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        for o in qs[:10000]:
            row = order_dict(o)
            writer.writerow(
                [(row[c] or {}).get("amount", "") if isinstance(row[c], dict) else (row[c] or "") for c in cols]
            )
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="transformaciones.csv"'
        return resp


class TransformationDetailView(TransformationListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        o = self.one("read", TransformationOrder.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(o), o)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        o = self.one("update", TransformationOrder.objects.all(), self.company(request), pk)
        self.if_match(request, o)
        self.manual_guard(o.transformation_type)
        data = parse_order(body(request), base=o)
        return self.command(
            request, "inventory.transformation.update", lambda u: (self.serialize(xf.update(u, o, data)), 200)
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        o = self.one("delete", TransformationOrder.objects.all(), self.company(request), pk)

        def run(u: Any) -> tuple[Any, int]:
            xf.delete(u, o)
            return None, 204

        return self.command(request, "inventory.transformation.delete", run)


ACTIONS = {
    "reserve": "update",
    "submit": "update",
    "approve": "approve",
    "reject": "approve",
    "cancel": "update",
    "post": "post",
    "reverse": "void",
    "simulate": "read",
}


class TransformationActionView(TransformationListView):
    def post(self, request: Request, pk: uuid.UUID, action: str) -> Response:  # type: ignore[override]
        if action not in ACTIONS:
            raise NotFound("unknown action")
        company = self.company(request)
        o = self.one(ACTIONS[action], TransformationOrder.objects.all(), company, pk)
        data = body(request)
        _only(data, {"reason", "posting_date", "note"})
        note = str(data.get("reason") or data.get("note") or "")
        day = _date(data.get("posting_date"), "posting_date")
        scope = f"inventory.transformation.{action}"
        if action == "simulate":
            readable, _w, _masks = self.get_simulation_fields()
            plan = xf.simulate(o)
            out = plan_dict(o, plan)
            visible = {k: v for k, v in out.items() if k in readable}
            return Response(_strip_costs(visible, "value_in"))
        if action == "post":
            self.manual_guard(o.transformation_type)
            return self.command(request, scope, lambda u: (self.serialize(xf.post(u, o, day)), 200))
        if action == "approve":
            return self.command(request, scope, lambda u: (self.serialize(xf.approve(u, o, note)), 200))
        if action == "reject":
            return self.command(request, scope, lambda u: (self.serialize(xf.reject(u, o, note)), 200))
        if action == "reverse":
            return self.command(request, scope, lambda u: (self.serialize(xf.reverse(u, o, note, day)), 200))
        fn = {"reserve": xf.reserve, "submit": xf.submit, "cancel": xf.cancel}[action]
        return self.command(request, scope, lambda u: (self.serialize(fn(u, o)), 200))

    def get_simulation_fields(self) -> tuple[set[str], set[str], dict[str, str]]:
        from iam.policy.engine import get_engine

        return get_engine().field_rules(self.ctx, "inventory.transformation_simulation")


# =========================================================================== portioning templates


def template_dict(t: PortioningTemplate) -> dict[str, Any]:
    outs = list(PortioningTemplateOutput.objects.filter(template_id=t.id).select_related("item").order_by("line_no"))
    return {
        "id": s(t.id),
        "tenant_id": s(t.tenant_id),
        "company_id": s(t.company_id),
        "code": t.code,
        "name": t.name,
        "status": t.status,
        "input_item_id": s(t.input_item_id),
        "tolerance_percent": s(t.tolerance_percent),
        "waste_percent": s(t.waste_percent),
        "rounding_rule": t.rounding_rule,
        "notes": t.notes,
        "outputs": {
            "lines": [
                {
                    "line_no": o.line_no,
                    "item_id": s(o.item_id),
                    "item_code": o.item.code,
                    "presentation_id": s(o.presentation_id),
                    "expected_yield_percent": s(o.expected_yield_percent),
                    "cost_share_percent": s(o.cost_share_percent),
                    "is_main": o.is_main,
                }
                for o in outs
            ]
        },
        "version": t.version,
    }


def parse_template(data: dict[str, Any], base: PortioningTemplate | None = None) -> xf.TemplateInput:
    _only(data, {"code", "name", "input_item_id", "outputs", "tolerance_percent", "waste_percent", "notes"})

    def pick(key: str, current: Any) -> Any:
        return data.get(key, current)

    item_id = uuid_or_none(pick("input_item_id", base.input_item_id if base else None), "input_item_id")
    if item_id is None:
        raise ValidationFailed("input_item_id required", pointer="/input_item_id", reason="required")
    raw = data.get("outputs")
    if raw is None and base is not None:
        outputs = tuple(
            xf.TemplateOutputInput(
                o.item_id, o.expected_yield_percent, o.cost_share_percent, o.is_main, o.presentation_id
            )
            for o in PortioningTemplateOutput.objects.filter(template_id=base.id).order_by("line_no")
        )
    else:
        rows = raw.get("lines") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise ValidationFailed("outputs must be {lines: [...]}", pointer="/outputs", reason="type")
        parsed: list[xf.TemplateOutputInput] = []
        for i, r in enumerate(rows):
            ptr = f"/outputs/lines/{i}"
            if not isinstance(r, dict):
                raise ValidationFailed("output must be an object", pointer=ptr, reason="type")
            _only(
                r,
                {"item_id", "expected_yield_percent", "cost_share_percent", "is_main", "presentation_id", "line_no"},
                ptr,
            )
            out_item = uuid_or_none(r.get("item_id"), f"{ptr}/item_id")
            if out_item is None:
                raise ValidationFailed("item_id required", pointer=f"{ptr}/item_id", reason="required")
            parsed.append(
                xf.TemplateOutputInput(
                    item_id=out_item,
                    expected_yield_percent=decimal(r.get("expected_yield_percent"), f"{ptr}/expected_yield_percent")
                    or Decimal(0),
                    cost_share_percent=decimal(
                        r.get("cost_share_percent"), f"{ptr}/cost_share_percent", required=False
                    ),
                    is_main=bool(r.get("is_main")),
                    presentation_id=uuid_or_none(r.get("presentation_id"), f"{ptr}/presentation_id"),
                )
            )
        outputs = tuple(parsed)
    return xf.TemplateInput(
        code=str(pick("code", base.code if base else "") or ""),
        name=str(pick("name", base.name if base else "") or ""),
        input_item_id=item_id,
        outputs=outputs,
        tolerance_percent=decimal(
            pick("tolerance_percent", s(base.tolerance_percent) if base else "2"), "tolerance_percent"
        )
        or Decimal(0),
        waste_percent=decimal(pick("waste_percent", s(base.waste_percent) if base else "0"), "waste_percent")
        or Decimal(0),
        notes=str(pick("notes", base.notes if base else "") or ""),
    )


class TemplateListView(InventoryView):
    perm, resource_type = "inventory.portioning_template", "inventory.portioning_template"

    def to_dict(self, obj: Any) -> dict[str, Any]:
        return template_dict(obj)

    def get(self, request: Request) -> Response:
        company = self.company(request)
        qs = self.scoped("read", PortioningTemplate.objects.all(), company)
        return self.paginate(
            request,
            qs,
            search_fields=("code", "name"),
            filter_fields={"status": "status", "input_item_id": "input_item_id"},
            sort_fields=("code", "name"),
        )

    def post(self, request: Request) -> Response:
        company = self.company(request)
        self.allow("create")
        data = parse_template(body(request))
        return self.command(
            request,
            "inventory.portioning_template.create",
            lambda u: (self.serialize(xf.create_template(u, company, data)), 201),
        )


class TemplateDetailView(TemplateListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        t = self.one("read", PortioningTemplate.objects.all(), self.company(request), pk)
        return self.etagged(self.serialize(t), t)

    def patch(self, request: Request, pk: uuid.UUID) -> Response:
        t = self.one("update", PortioningTemplate.objects.all(), self.company(request), pk)
        self.if_match(request, t)
        data = parse_template(body(request), base=t)
        return self.command(
            request,
            "inventory.portioning_template.update",
            lambda u: (self.serialize(xf.update_template(u, t, data)), 200),
        )

    def delete(self, request: Request, pk: uuid.UUID) -> Response:
        t = self.one("delete", PortioningTemplate.objects.all(), self.company(request), pk)

        def run(u: Any) -> tuple[Any, int]:
            xf.delete_template(u, t)
            return None, 204

        return self.command(request, "inventory.portioning_template.delete", run)


# =========================================================================== repack rules (derived, read-only)


def rule_dict(r: xf.RepackRule) -> dict[str, Any]:
    return {
        "id": s(r.id),
        "tenant_id": s(r.tenant_id),
        "company_id": s(r.company_id),
        "status": r.status,
        "item_id": s(r.item_id),
        "from_presentation_id": s(r.from_presentation_id),
        "to_presentation_id": s(r.to_presentation_id),
        "from_factor": s(r.from_factor),
        "to_factor": s(r.to_factor),
        "version": r.version,
    }


class RepackRuleListView(InventoryView):
    perm, resource_type = "inventory.repack_rule", "inventory.repack_rule"
    ignored_params = ("item_id",)

    def _rules(self, request: Request) -> list[xf.RepackRule]:
        company = self.company(request)
        self.allow("read")
        item_id = uuid_or_none(request.query_params.get("item_id"), "item_id")
        return xf.repack_rules(company, item_id)

    def get(self, request: Request) -> Response:
        rules = self._rules(request)
        return Response({"items": [self.serialize(rule_dict(r)) for r in rules], "next_cursor": None})


class RepackRuleDetailView(RepackRuleListView):
    def get(self, request: Request, pk: uuid.UUID) -> Response:  # type: ignore[override]
        for r in self._rules(request):
            if r.id == pk:
                return Response(self.serialize(rule_dict(r)))
        raise NotFound("not found")
