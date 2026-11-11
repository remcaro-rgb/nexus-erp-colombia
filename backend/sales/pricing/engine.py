"""Price resolver (F-SAL-PRICING, D-024): manual override > active price formula > price list > item base price.

* Every document line and ``POST /api/v1/sales/pricing:quote`` call the same :func:`resolve`.
* Formulas are ``workflow`` formulas of domain ``pricing`` (P07 sandbox, D-008), bound to a scope by
  :class:`sales.models.PriceFormula`. Only ACTIVE versions effective on the document date are evaluated; the
  evaluation snapshot (formula id + version + hash, inputs, trace) is stored by P07 (``FormulaEvaluation``)
  and copied into the line's ``price_snapshot``.
* Money inputs of a formula (``base_price``, ``list_price``, ``cost``) are in the company's functional currency
  (COP); the result is converted to the document currency with the TRM of the document date and rounded
  ROUND_HALF_UP to the currency's minor unit (D-001).
* ``cost`` is a confidential input (inventory valuation): the formula may read it, but the snapshot shown to a
  principal without the field grant drops it and the trace (:func:`visible_snapshot`); portal projections never
  carry snapshots at all (only the final price).
* Overrides beyond the principal's discount limit (``DiscountLimit`` / company default) are flagged
  ``PENDING_APPROVAL``; the document then needs a ``sales.price_override`` approval before it can be confirmed or
  posted (``pricing_override_requires_approval``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db.models import Q

from kernel.interfaces.policy import PolicyService
from kernel.money import minor_units, q_pct, round_half_up
from kernel.tenancy import TenantContext
from kernel.uow import UnitOfWork
from sales.errors import PriceNotFound, PricingFormulaMissingInput
from sales.models import DiscountLimit, ItemSalesPrice, OverrideStatus, Party, PriceFormula, PriceList, PriceListLine
from sales.services.common import policy, rate_on, settings_for

OVERRIDE, FORMULA, LIST, BASE = "OVERRIDE", "FORMULA", "LIST", "BASE"
PRECEDENCE = (OVERRIDE, FORMULA, LIST, BASE)
PRICE_PLACES = 6
#: Inputs a pricing formula may declare (D-024 / FEATURES-ADDENDUM F-SAL-PRICING data model).
KNOWN_INPUTS = (
    "base_price",
    "list_price",
    "cost",
    "qty",
    "uom",
    "customer_group",
    "customer_id",
    "currency",
    "fx_rate",
    "date",
    "channel",
    "category",
)
CONFIDENTIAL_INPUTS = frozenset({"cost"})
#: Field-policy resource of the price snapshot (cost and trace are ``confidential``).
SNAPSHOT_RESOURCE = "sales.price_snapshot"


@dataclass(frozen=True, slots=True)
class PriceRequest:
    item_id: uuid.UUID
    quantity: Decimal
    on: dt.date
    currency: str = "COP"
    party: Party | None = None
    channel: str = "internal"
    uom: str = "UND"
    fx_rate: Decimal | None = None  # document rate (COP per unit); default = ledger TRM on ``on``
    override_price: Decimal | None = None
    override_reason: str = ""
    discount_pct: Decimal = Decimal(0)  # manual line discount (an override of the resolved price)
    document_type: str = ""
    document_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class PriceResult:
    unit_price: Decimal  # final unit price before line discount, document currency
    source: str
    resolved_price: Decimal | None  # price the precedence chain gives without override
    discount_pct: Decimal  # effective discount vs the resolved price (override and/or line discount)
    override_status: str
    currency: str
    fx_rate: Decimal
    snapshot: dict[str, Any] = field(default_factory=dict)

    def net_unit_price(self) -> Decimal:
        return self.unit_price


def _hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def round_price(value: Decimal, currency: str) -> Decimal:
    """Rounding per currency: ROUND_HALF_UP to the ISO 4217 minor unit (COP 2, USD 2, JPY 0; D-001)."""
    return round_half_up(value, minor_units(currency))


def rounding_rule(currency: str) -> str:
    return f"ROUND_HALF_UP:{minor_units(currency)}"


# --------------------------------------------------------------------------- level lookups


def _effective(qs: Any, on: dt.date) -> Any:
    return qs.filter(valid_from__lte=on).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on))


def _to_doc(amount_cop: Decimal, fx: Decimal) -> Decimal:
    return amount_cop if fx == 1 else amount_cop / fx


def base_price(
    ctx: TenantContext, company_id: uuid.UUID, req: PriceRequest, fx: Decimal
) -> tuple[Decimal, dict[str, Any]] | None:
    """Item base price in the document currency (a price in that currency first, else COP converted)."""
    rows = _effective(
        ItemSalesPrice.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, item_id=req.item_id), req.on
    )
    for currency in dict.fromkeys((req.currency, "COP")):
        row = rows.filter(currency=currency).order_by("-valid_from").first()
        if row is None:
            continue
        if currency == req.currency:
            return row.base_price, {"id": str(row.id), "currency": currency, "price": f"{row.base_price:f}"}
        return _to_doc(row.base_price, fx), {"id": str(row.id), "currency": "COP", "price": f"{row.base_price:f}"}
    return None


def base_price_cop(ctx: TenantContext, company_id: uuid.UUID, item_id: uuid.UUID, on: dt.date) -> Decimal | None:
    row = (
        _effective(
            ItemSalesPrice.objects.filter(
                tenant_id=ctx.tenant_id, company_id=company_id, item_id=item_id, currency="COP"
            ),
            on,
        )
        .order_by("-valid_from")
        .first()
    )
    return None if row is None else row.base_price


def item_category(ctx: TenantContext, company_id: uuid.UUID, item_id: uuid.UUID, on: dt.date) -> str:
    row = (
        _effective(ItemSalesPrice.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, item_id=item_id), on)
        .exclude(category="")
        .order_by("-valid_from")
        .first()
    )
    if row is not None:
        return str(row.category)
    from django.apps import apps

    if apps.is_installed("inventory"):
        item = apps.get_model("inventory", "Item").objects.filter(company_id=company_id, id=item_id).first()
        if item is not None:
            return str(item.category or "")
    return ""


def _candidate_lists(ctx: TenantContext, company_id: uuid.UUID, req: PriceRequest) -> list[PriceList]:
    """The customer's own price list first, then lists of the customer's group, then generic lists."""
    qs = _effective(
        PriceList.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status=PriceList.Status.ACTIVE), req.on
    )
    out: list[PriceList] = []
    party = req.party
    if party is not None and party.price_list_id is not None:
        own = qs.filter(id=party.price_list_id).first()
        if own is not None:
            out.append(own)
    group = party.customer_group if party is not None else ""
    if group:
        out += [p for p in qs.filter(customer_group=group).order_by("code") if p not in out]
    out += [p for p in qs.filter(customer_group="").order_by("code") if p not in out]
    return out


def list_price(
    ctx: TenantContext, company_id: uuid.UUID, req: PriceRequest, fx: Decimal
) -> tuple[Decimal, dict[str, Any]] | None:
    """Best price-list line: list order above, then the highest quantity break ≤ quantity."""
    for plist in _candidate_lists(ctx, company_id, req):
        if plist.currency not in (req.currency, "COP"):
            continue
        line = (
            PriceListLine.objects.filter(price_list=plist, item_id=req.item_id, min_qty__lte=req.quantity)
            .order_by("-min_qty")
            .first()
        )
        if line is None:
            continue
        price = line.unit_price if plist.currency == req.currency else _to_doc(line.unit_price, fx)
        return price, {
            "price_list_id": str(plist.id),
            "price_list_code": plist.code,
            "customer_group": plist.customer_group,
            "currency": plist.currency,
            "min_qty": f"{line.min_qty:f}",
            "price": f"{line.unit_price:f}",
        }
    return None


def list_price_cop(ctx: TenantContext, company_id: uuid.UUID, req: PriceRequest) -> Decimal | None:
    for plist in _candidate_lists(ctx, company_id, req):
        if plist.currency != "COP":
            continue
        line = (
            PriceListLine.objects.filter(price_list=plist, item_id=req.item_id, min_qty__lte=req.quantity)
            .order_by("-min_qty")
            .first()
        )
        if line is not None:
            return Decimal(line.unit_price)
    return None


def _scope_matches(scope: dict[str, Any], facts: dict[str, Any]) -> bool:
    for key, wanted in scope.items():
        if wanted in (None, "", []):
            continue
        have = facts.get(key)
        if isinstance(wanted, list):
            if str(have) not in {str(w) for w in wanted}:
                return False
        elif str(have) != str(wanted):
            return False
    return True


def applicable_formula(
    ctx: TenantContext, company_id: uuid.UUID, req: PriceRequest, category: str
) -> PriceFormula | None:
    """Highest priority ACTIVE binding whose scope matches; ties: more specific scope, then code."""
    party = req.party
    facts = {
        "item_id": str(req.item_id),
        "category": category,
        "customer_group": party.customer_group if party else "",
        "customer_id": str(party.id) if party else "",
        "currency": req.currency,
        "channel": req.channel,
        "service": str(req.item_id),
    }
    rows = _effective(
        PriceFormula.objects.filter(tenant_id=ctx.tenant_id, company_id=company_id, status=PriceFormula.Status.ACTIVE),
        req.on,
    )
    matches = [r for r in rows if _scope_matches(dict(r.scope or {}), facts)]
    if not matches:
        return None
    matches.sort(
        key=lambda r: (-r.priority, -len([v for v in (r.scope or {}).values() if v not in (None, "", [])]), r.code)
    )
    best: PriceFormula = matches[0]
    return best


def cost_cop(company_id: uuid.UUID, item_id: uuid.UUID) -> Decimal | None:
    """Current unit cost (P09 valuation: average cost, else standard cost) — confidential input."""
    from django.apps import apps

    if not apps.is_installed("inventory"):
        return None
    valuation = (
        apps.get_model("inventory", "ItemValuation").objects.filter(company_id=company_id, item_id=item_id).first()
    )
    if valuation is not None and valuation.qty > 0:
        return Decimal(valuation.avg_cost)
    item = apps.get_model("inventory", "Item").objects.filter(company_id=company_id, id=item_id).first()
    if item is not None and item.standard_cost is not None:
        return Decimal(item.standard_cost)
    return None


def _formula_price(
    uow: UnitOfWork, company_id: uuid.UUID, binding: PriceFormula, req: PriceRequest, fx: Decimal, category: str
) -> tuple[Decimal, dict[str, Any]]:
    from workflow.formula.lifecycle import FormulaService

    ctx = uow.ctx
    service = FormulaService()
    definition = next(
        (
            d
            for d in service.active_definitions(ctx.tenant_id, company_id, "pricing", req.on)
            if d.code == binding.formula_code
        ),
        None,
    )
    if definition is None:
        from workflow.formula.errors import FormulaNotApproved

        raise FormulaNotApproved(
            f"no ACTIVE version of pricing formula '{binding.formula_code}' on {req.on.isoformat()}"
        )
    party = req.party
    available: dict[str, Any] = {
        "base_price": base_price_cop(ctx, company_id, req.item_id, req.on),
        "list_price": list_price_cop(ctx, company_id, req),
        "cost": cost_cop(company_id, req.item_id),
        "qty": req.quantity,
        "uom": req.uom,
        "customer_group": party.customer_group if party else "",
        "customer_id": str(party.id) if party else "",
        "currency": req.currency,
        "fx_rate": fx,
        "date": req.on,
        "channel": req.channel,
        "category": category,
    }
    inputs: dict[str, Any] = {}
    missing: list[str] = []
    for name, spec in definition.schema.fields.items():
        value = available.get(name)
        if name not in available or value is None or value == "":
            if spec.required and spec.default is None:
                missing.append(name)
            continue
        inputs[name] = value
    if missing:
        raise PricingFormulaMissingInput(
            f"pricing formula '{binding.formula_code}' needs {', '.join(sorted(missing))}",
            missing=sorted(missing),
            formula=binding.code,
        )
    result, snap = service.evaluate_for_document(
        uow,
        company_id,
        "pricing",
        binding.formula_code,
        inputs,
        on=req.on,
        document_type=req.document_type,
        document_id=req.document_id,
    )
    value_cop = Decimal(result.value)  # type: ignore[arg-type]
    snapshot = {
        "binding_id": str(binding.id),
        "binding_code": binding.code,
        "formula_id": str(snap.formula_id),
        "formula_code": binding.formula_code,
        "formula_version": result.version,
        "formula_hash": result.formula_hash,
        "evaluation_id": str(snap.id),
        "inputs": {k: (f"{v:f}" if isinstance(v, Decimal) else str(v)) for k, v in sorted(inputs.items())},
        "trace": [{"expr": t.expr, "value": str(t.value)} for t in result.trace],
        "result_cop": f"{value_cop:f}",
    }
    return _to_doc(value_cop, fx), snapshot


def discount_limit(ctx: TenantContext, company_id: uuid.UUID) -> Decimal:
    row = DiscountLimit.objects.filter(
        tenant_id=ctx.tenant_id, company_id=company_id, principal_id=ctx.principal_id
    ).first()
    if row is not None:
        return Decimal(row.max_discount_pct)
    return Decimal(settings_for(ctx, company_id).default_discount_limit_pct)


def document_fx(uow: UnitOfWork, company_id: uuid.UUID, currency: str, on: dt.date, given: Decimal | None) -> Decimal:
    if currency == "COP":
        return Decimal(1)
    if given is not None and given > 0:
        return given
    rate = rate_on(uow, company_id, currency, on)
    if rate is None or rate <= 0:
        from kernel.errors import ValidationFailed

        raise ValidationFailed(f"no exchange rate (TRM) for {currency} on or before {on.isoformat()}", field="fx_rate")
    return rate


def resolve(uow: UnitOfWork, company_id: uuid.UUID, req: PriceRequest) -> PriceResult:
    """Resolve the unit price of one line (D-024). Writes only the P07 evaluation snapshot (caller's UoW)."""
    ctx = uow.ctx
    ctx.require_company(company_id)
    fx = document_fx(uow, company_id, req.currency, req.on, req.fx_rate)
    category = item_category(ctx, company_id, req.item_id, req.on)
    levels: list[dict[str, Any]] = []
    resolved: Decimal | None = None
    source = ""
    detail: dict[str, Any] = {}
    binding = applicable_formula(ctx, company_id, req, category)
    if binding is not None:
        price, detail = _formula_price(uow, company_id, binding, req, fx, category)
        resolved, source = price, FORMULA
    else:
        levels.append({"level": FORMULA, "applied": False})
        found = list_price(ctx, company_id, req, fx)
        if found is not None:
            resolved, detail = found
            source = LIST
        else:
            levels.append({"level": LIST, "applied": False})
            base = base_price(ctx, company_id, req, fx)
            if base is not None:
                resolved, detail = base
                source = BASE
    if resolved is not None:
        resolved = round_price(resolved, req.currency)
    unit_price = resolved
    status = OverrideStatus.NONE
    discount = q_pct(req.discount_pct)
    override: dict[str, Any] | None = None
    if req.override_price is not None:
        unit_price = round_half_up(req.override_price, PRICE_PLACES)
        source = OVERRIDE
    if unit_price is None:
        raise PriceNotFound(
            "no override, formula, price list or base price applies to the item", item_id=str(req.item_id)
        )
    if req.override_price is not None or discount > 0:
        effective = unit_price * (Decimal(100) - discount) / Decimal(100)
        if resolved is None or resolved == 0:
            effective_pct = Decimal(100)
        else:
            effective_pct = q_pct(max(Decimal(0), (resolved - effective) * Decimal(100) / resolved))
        limit = discount_limit(ctx, company_id)
        status = OverrideStatus.WITHIN_LIMIT if effective_pct <= limit else OverrideStatus.PENDING_APPROVAL
        override = {
            "user": str(ctx.principal_id),
            "reason": req.override_reason[:500],
            "override_price": f"{unit_price:f}" if req.override_price is not None else None,
            "line_discount_pct": f"{discount:f}",
            "effective_discount_pct": f"{effective_pct:f}",
            "limit_pct": f"{limit:f}",
            "approval": status,
        }
        discount_out = effective_pct
    else:
        discount_out = Decimal(0)
    before_tax = round_half_up(
        unit_price * req.quantity * (Decimal(100) - discount) / Decimal(100), minor_units(req.currency)
    )
    snapshot = {
        "source": source,
        "precedence": list(PRECEDENCE),
        "skipped": levels,
        "resolved_source": FORMULA
        if binding is not None
        else (LIST if "price_list_id" in detail else (BASE if resolved is not None else None)),
        "resolved_price": f"{resolved:f}" if resolved is not None else None,
        "unit_price": f"{unit_price:f}",
        "currency": req.currency,
        "fx_rate": f"{fx:f}",
        "rate_date": req.on.isoformat(),
        "rounding": rounding_rule(req.currency),
        "quantity": f"{req.quantity:f}",
        "detail": detail,
        "override": override,
        "result_before_tax": f"{before_tax:f}",
        "priced_on": req.on.isoformat(),
    }
    snapshot["hash"] = _hash(snapshot)
    return PriceResult(unit_price, source, resolved, discount_out, status, req.currency, fx, snapshot)


def visible_snapshot(ctx: TenantContext, snapshot: dict[str, Any], pol: PolicyService | None = None) -> dict[str, Any]:
    """Snapshot as a principal may read it: ``cost`` input and the trace only with the confidential field grant."""
    if not snapshot:
        return {}
    mask = (pol or policy()).field_mask(ctx, SNAPSHOT_RESOURCE)
    out = json.loads(json.dumps(snapshot))
    detail = out.get("detail") or {}
    if "cost" not in mask.readable:
        inputs = detail.get("inputs")
        if isinstance(inputs, dict):
            for key in CONFIDENTIAL_INPUTS:
                inputs.pop(key, None)
        if "trace" not in mask.readable:
            detail.pop("trace", None)
    elif "trace" not in mask.readable:
        detail.pop("trace", None)
    return dict(out)


def explain(result: PriceResult) -> dict[str, Any]:
    """Customer-facing explanation: the final price only (FEATURES-ADDENDUM F-SAL-PRICING › Security)."""
    return {"unit_price": f"{result.unit_price:f}", "currency": result.currency}


__all__ = [
    "BASE",
    "FORMULA",
    "LIST",
    "OVERRIDE",
    "PRECEDENCE",
    "ROUND_HALF_UP",
    "PriceRequest",
    "PriceResult",
    "explain",
    "resolve",
    "round_price",
    "visible_snapshot",
]
