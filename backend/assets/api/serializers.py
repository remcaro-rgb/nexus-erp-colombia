"""Contract representations (contracts/openapi/modules/assets.yaml, additionalProperties: false)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from assets.models import (
    SLOTS,
    Asset,
    AssetBook,
    AssetCategory,
    DepreciationLine,
    DepreciationRun,
    DocumentBase,
    Impairment,
    PhysicalCount,
    Revaluation,
)


def money(value: Decimal | None, currency: str = "COP") -> dict[str, str]:
    return {"amount": f"{(value or Decimal(0)):.4f}", "currency": currency}


def audit(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("created_at", "created_by", "updated_at", "updated_by"):
        value = getattr(obj, key, None)
        if value is not None:
            out[key] = value
    return out


def dimensions(obj: Any, branch: bool = False) -> dict[str, Any]:
    out = {s: getattr(obj, s, None) for s in SLOTS}
    if branch:
        out["branch_id"] = getattr(obj, "branch_id", None)
    return out


def category(obj: AssetCategory) -> dict[str, Any]:
    return {
        "id": obj.id,
        "tenant_id": obj.tenant_id,
        "company_id": obj.company_id,
        "code": obj.code,
        "name": obj.name,
        "status": obj.status,
        "useful_life_months": obj.useful_life_months,
        "method": obj.method,
        "account_role": obj.account_role or "FIXED_ASSET_COST",
        "version": obj.version,
        "audit": audit(obj),
    }


def book(obj: AssetBook) -> dict[str, Any]:
    return {
        "id": obj.id,
        "tenant_id": obj.tenant_id,
        "company_id": obj.company_id,
        "code": obj.code,
        "name": obj.name,
        "status": obj.status,
        "framework": obj.framework,
        "version": obj.version,
        "audit": audit(obj),
    }


def asset(obj: Asset) -> dict[str, Any]:
    return {
        "id": obj.id,
        "tenant_id": obj.tenant_id,
        "company_id": obj.company_id,
        "code": obj.code,
        "name": obj.name,
        "status": obj.contract_status,
        "category_id": obj.category_id,
        "acquisition_cost": money(obj.acquisition_cost, obj.currency),
        "acquisition_date": obj.acquisition_date,
        "location": obj.location,
        "custodian_employee_id": obj.custodian_employee_id,
        "dimensions": dimensions(obj, branch=True),
        "version": obj.version,
        "audit": audit(obj),
    }


def _document(obj: DocumentBase, source_type: str) -> dict[str, Any]:
    return {
        "id": obj.id,
        "tenant_id": obj.tenant_id,
        "company_id": obj.company_id,
        "number": obj.number,
        "document_date": obj.document_date,
        "posting_date": obj.posting_date,
        "status": obj.status,
        "currency": obj.currency,
        "total": money(obj.total, obj.currency),
        "notes": obj.notes,
        "source": {"type": source_type, "id": obj.id, "number": obj.number or None},
        "version": obj.version,
        "audit": audit(obj),
    }


def _line(raw: dict[str, Any], n: int) -> dict[str, Any]:
    allowed = (
        "line_no",
        "item_id",
        "description",
        "quantity",
        "unit_price",
        "amount",
        "tax_code",
        "account_code",
        "dimensions",
    )
    out = {k: raw[k] for k in allowed if k in raw}
    out.setdefault("line_no", n)
    return out


def run(obj: DepreciationRun, detail: list[DepreciationLine] | None = None) -> dict[str, Any]:
    body = _document(obj, "assets.depreciation_run")
    if detail:
        lines = [
            {
                "line_no": d.line_no,
                "item_id": d.asset_id,
                "description": d.asset.code,
                "quantity": {"value": f"{d.units:.6f}", "uom": "UND"} if d.units is not None else None,
                "amount": money(d.amount),
                "account_code": d.expense_role,
                "dimensions": dimensions(d),
            }
            for d in detail
        ]
        body["lines"] = [{k: v for k, v in ln.items() if v is not None} for ln in lines]
    else:
        body["lines"] = [_line(ln, i) for i, ln in enumerate(obj.lines, start=1)]
    body.update(period_id=obj.period_id, book_id=obj.book_id)
    return body


def valuation(obj: Revaluation | Impairment) -> dict[str, Any]:
    kind = "assets.revaluation" if isinstance(obj, Revaluation) else "assets.impairment"
    body = _document(obj, kind)
    body.update(asset_id=obj.asset_id, lines=[_line(ln, i) for i, ln in enumerate(obj.lines, start=1)])
    return body


def count(obj: PhysicalCount) -> dict[str, Any]:
    body = _document(obj, "assets.physical_count")
    body["lines"] = [_line(ln, i) for i, ln in enumerate(obj.lines, start=1)]
    return body
