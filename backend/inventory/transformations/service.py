"""Transformation engine (F-INV-XFORM): presentation repack, portioning, manual and BOM production orders.

Every command runs in the caller's unit of work. ``post`` writes, in **one** transaction, the issue moves of the
inputs, the receipt moves of the outputs (valuation in-line through ``InventoryStockService``) and the journal entry
(``PostingService``); any failure (stock, valuation, ledger) rolls everything back, and the reservations made by an
earlier ``:reserve`` stay (they were committed by that earlier command).

Locking order (shared/07 §7): the order row, then the ``inventory_item_valuation`` rows of **every** item of the
document in ascending id order (inputs and outputs together, before the stock service locks anything), then quants
in the stock service's order. A concurrent sale of the same lot locks the same valuation row first, so the two
serialize and the availability re-check under lock lets exactly one of them take units that only one can have.

Costing (D-016, ``transformations.core``): value in = Σ issued input value (actual moves) + additional costs; abnormal
waste is expensed (Dr ``SCRAP_EXPENSE``), normal waste is absorbed; the rest is allocated to the outputs by cost
share % / relative standard value / quantity with the rounding remainder on the main output, and every output is
received at a unit cost whose receipt value is exactly its allocation. ``Σ out + expensed waste = Σ in + costs``.

GL (FEATURES-ADDENDUM F-INV-XFORM posting): inventory lines are netted per inventory account role — the role of an
item is ``INVENTORY_<CATEGORY>`` when the company maps that role, else ``INVENTORY`` — so a value-neutral repack or a
transformation within one valuation account posts no inventory lines (no entry at all when nothing else remains);
different accounts give Dr output inventory / Cr input inventory; abnormal waste Dr ``SCRAP_EXPENSE`` / Cr inventory;
additional costs Dr inventory / Cr ``TRANSFORMATION_COST_CLEARING``.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from django.db.models import F

from inventory.bom import core as bom_core
from inventory.bom import service as boms
from inventory.bom.models import Bom
from inventory.common import audit
from inventory.errors import error
from inventory.locations import service as locations
from inventory.masters import quantity_in_base
from inventory.models import (
    Item,
    ItemValuation,
    Location,
    Lot,
    PackagedQuant,
    Presentation,
    Reservation,
    Status,
    StockMove,
    StockQuant,
    Warehouse,
)
from inventory.presentations import core as pres
from inventory.stock.service import SERVICE as STOCK
from inventory.transformations import core
from inventory.transformations.models import (
    PortioningTemplate,
    PortioningTemplateOutput,
    TransformationLine,
    TransformationOrder,
)
from inventory.valuation import core as val
from kernel import clock, sequences, services
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.interfaces.common import DocumentRef
from kernel.interfaces.posting import PostingLine, PostingRequest, PostingService
from kernel.interfaces.stock import StockLine
from kernel.models import Sequence
from kernel.money import q_amount, q_qty, round_half_up
from kernel.uow import UnitOfWork

SOURCE_TYPE = "inventory.transformation"
REVERSAL_SOURCE_TYPE = "inventory.transformation.reversal"
SEQUENCE_CODE, PREFIX = "TRF", "TRF-"
INVENTORY_ROLE = "INVENTORY"
WASTE_ROLE = "SCRAP_EXPENSE"
CLEARING_ROLE = "TRANSFORMATION_COST_CLEARING"
GAIN_ROLE, LOSS_ROLE = "INVENTORY_ADJ_GAIN", "INVENTORY_ADJ_LOSS"
DEFAULT_TOLERANCE = Decimal(2)  # D-015
MANUAL_APPROVAL_THRESHOLD = Decimal(1_000_000)  # COP, FEATURES-ADDENDUM F-INV-XFORM default
ZERO = Decimal(0)
HUNDRED = Decimal(100)
K = TransformationOrder.Kind
S = TransformationOrder.OrderStatus
R = TransformationLine.Role


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True)
class LineInput:
    role: str
    item_id: uuid.UUID | None = None
    quantity: Decimal | None = None
    qty_packages: Decimal | None = None
    presentation_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    lot_code: str = ""
    actual_weight: Decimal | None = None
    cost_share: Decimal | None = None
    is_main: bool = False
    is_byproduct: bool = False
    abnormal: bool = False


@dataclass(frozen=True, slots=True)
class OrderInput:
    transformation_type: str
    warehouse_id: uuid.UUID
    document_date: dt.date
    lines: tuple[LineInput, ...] = ()
    template_id: uuid.UUID | None = None
    bom_id: uuid.UUID | None = None
    planned_qty: Decimal | None = None
    additional_costs: Decimal = ZERO
    tolerance_percent: Decimal | None = None
    notes: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TemplateOutputInput:
    item_id: uuid.UUID
    expected_yield_percent: Decimal
    cost_share_percent: Decimal | None = None
    is_main: bool = False
    presentation_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class TemplateInput:
    code: str
    name: str
    input_item_id: uuid.UUID
    outputs: tuple[TemplateOutputInput, ...]
    tolerance_percent: Decimal = DEFAULT_TOLERANCE
    waste_percent: Decimal = ZERO
    notes: str = ""


# --------------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class JournalPreview:
    account_role: str
    side: Literal["DEBIT", "CREDIT"]
    amount: Decimal


@dataclass(slots=True)
class Plan:
    """Costing plan of an order (simulation or the values frozen at post)."""

    value_in: Decimal = ZERO
    additional_costs: Decimal = ZERO
    waste_expensed: Decimal = ZERO
    value_out: Decimal = ZERO
    allocation_basis: str = ""
    input_values: dict[int, Decimal] = field(default_factory=dict)
    output_values: dict[int, Decimal] = field(default_factory=dict)
    abnormal_waste_qty: Decimal = ZERO
    waste_values: dict[int, Decimal] = field(default_factory=dict)
    approval_required: bool = False
    approval_reasons: list[str] = field(default_factory=list)
    yield_checks: list[core.YieldCheck] = field(default_factory=list)
    journal: list[JournalPreview] = field(default_factory=list)
    qty_in: Decimal = ZERO
    qty_out: Decimal = ZERO

    @property
    def balanced(self) -> bool:
        return self.value_out + self.waste_expensed == self.value_in + self.additional_costs


# --------------------------------------------------------------------------- helpers


def _doc(o: TransformationOrder) -> DocumentRef:
    return DocumentRef(SOURCE_TYPE, o.id, o.number or None)


def _number(uow: UnitOfWork, company_id: uuid.UUID, day: dt.date) -> str:
    Sequence.objects.get_or_create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        sequence_code=SEQUENCE_CODE,
        prefix=PREFIX,
        defaults={"padding": 6},
    )
    return sequences.allocate(company_id, SEQUENCE_CODE, PREFIX, on_date=day).formatted


def _cross(detail: str) -> Exception:
    return error("transformation_cross_scope", detail)


def _warehouse(uow: UnitOfWork, company_id: uuid.UUID, warehouse_id: uuid.UUID) -> Warehouse:
    wh = Warehouse.objects.filter(tenant_id=uow.ctx.tenant_id, id=warehouse_id).first()
    if wh is None or wh.company_id != company_id:
        raise _cross("the warehouse belongs to another company or is unknown")
    if wh.status != Status.ACTIVE:
        raise ValidationFailed("the warehouse is archived", pointer="/warehouse_id", reason="archived")
    return wh


def _item(uow: UnitOfWork, company_id: uuid.UUID, item_id: uuid.UUID | None, ptr: str) -> Item:
    if item_id is None:
        raise ValidationFailed("item_id required", pointer=f"{ptr}/item_id", reason="required")
    item = Item.objects.select_related("base_uom").filter(tenant_id=uow.ctx.tenant_id, id=item_id).first()
    if item is None or item.company_id != company_id:
        raise _cross(f"{ptr}: item of another company or unknown")
    if item.item_type not in (Item.ItemType.STOCKABLE, Item.ItemType.CONSUMABLE):
        raise ValidationFailed("only stockable/consumable items transform", pointer=f"{ptr}/item_id", reason="type")
    return item


def _location(company_id: uuid.UUID, wh: Warehouse, location_id: uuid.UUID | None, ptr: str) -> Location | None:
    if location_id is None:
        return None
    loc = Location.objects.filter(id=location_id).first()
    if loc is None or loc.company_id != company_id or loc.warehouse_id != wh.id:
        raise _cross(f"{ptr}: location outside warehouse {wh.code}")
    return locations.assert_postable(loc, wh.id)


def _lot(item: Item, lot_id: uuid.UUID | None, ptr: str) -> Lot | None:
    if lot_id is None:
        return None
    lot = Lot.objects.filter(company_id=item.company_id, item_id=item.id, id=lot_id).first()
    if lot is None:
        raise ValidationFailed("unknown lot for this item", pointer=f"{ptr}/lot_id", reason="not_found")
    return lot


def _presentation(item: Item, presentation_id: uuid.UUID | None, ptr: str) -> Presentation | None:
    if presentation_id is None:
        return None
    p = Presentation.objects.filter(company_id=item.company_id, item_id=item.id, id=presentation_id).first()
    if p is None:
        raise ValidationFailed("unknown presentation for this item", pointer=f"{ptr}/presentation_id")
    if p.status != Status.ACTIVE:
        raise ValidationFailed("the presentation is archived", pointer=f"{ptr}/presentation_id", reason="archived")
    return p


def _lines(o: TransformationOrder) -> list[TransformationLine]:
    return list(
        TransformationLine.objects.filter(order_id=o.id)
        .select_related("item__base_uom", "presentation", "location", "lot")
        .order_by("line_no")
    )


def _weight(ln: TransformationLine) -> Decimal:
    return Decimal(ln.actual_weight if ln.actual_weight is not None else ln.quantity)


def _lock(o: TransformationOrder) -> TransformationOrder:
    locked = (
        TransformationOrder.objects.select_for_update(of=("self",))
        .select_related("warehouse", "template", "bom")
        .filter(id=o.id)
        .first()
    )
    if locked is None:
        raise NotFound("transformation not found")
    return locked


def _require(o: TransformationOrder, *allowed: str) -> None:
    if o.status not in allowed:
        raise Conflict(
            f"transformation is {o.status}; expected {' or '.join(allowed)}", code="invalid_state_transition"
        )


def _event(uow: UnitOfWork, o: TransformationOrder) -> None:
    uow.emit(
        "inventory.transformation.posted.v1",
        {
            "transformation_id": str(o.id),
            "number": o.number,
            "value_in": {"amount": format(q_amount((o.value_in or ZERO) + o.additional_costs), "f"), "currency": "COP"},
            "value_out": {
                "amount": format(q_amount((o.value_out or ZERO) + (o.waste_expensed or ZERO)), "f"),
                "currency": "COP",
            },
            **({"journal_entry_id": str(o.journal_entry_id)} if o.journal_entry_id else {}),
        },
        company_id=o.company_id,
    )


# --------------------------------------------------------------------------- templates


def _template_outputs(
    uow: UnitOfWork, company_id: uuid.UUID, tpl: PortioningTemplate, outs: tuple[TemplateOutputInput, ...]
) -> None:
    if not outs or len(outs) > 100:
        raise ValidationFailed("1–100 template outputs required", pointer="/outputs", reason="required")
    shares = [o.cost_share_percent for o in outs]
    if any(s is not None for s in shares) and (None in shares or sum((s or ZERO for s in shares), ZERO) != HUNDRED):
        raise ValidationFailed("cost_share_percent must be set on every output and sum 100", pointer="/outputs")
    if sum((o.expected_yield_percent for o in outs), ZERO) + tpl.waste_percent > HUNDRED:
        raise ValidationFailed("Σ expected yield + waste exceeds 100 %", pointer="/outputs", reason="range")
    if sum(1 for o in outs if o.is_main) > 1:
        raise ValidationFailed("only one main output", pointer="/outputs", reason="main")
    PortioningTemplateOutput.objects.filter(template_id=tpl.id).delete()
    for n, o in enumerate(outs, start=1):
        ptr = f"/outputs/lines/{n - 1}"
        item = _item(uow, company_id, o.item_id, ptr)
        if not ZERO < o.expected_yield_percent <= HUNDRED:
            raise ValidationFailed(
                "expected_yield_percent must be in (0, 100]", pointer=f"{ptr}/expected_yield_percent"
            )
        PortioningTemplateOutput.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company_id,
            template=tpl,
            line_no=n,
            item=item,
            presentation=_presentation(item, o.presentation_id, ptr),
            expected_yield_percent=o.expected_yield_percent,
            cost_share_percent=o.cost_share_percent,
            is_main=o.is_main,
        )


def _check_template(data: TemplateInput) -> None:
    if not data.code.strip() or len(data.code) > 40:
        raise ValidationFailed("code is required (≤ 40 characters)", pointer="/code", reason="required")
    if not data.name.strip():
        raise ValidationFailed("name is required", pointer="/name", reason="required")
    if not ZERO <= data.tolerance_percent <= HUNDRED:
        raise ValidationFailed("tolerance_percent must be in [0, 100]", pointer="/tolerance_percent")
    if not ZERO <= data.waste_percent < HUNDRED:
        raise ValidationFailed("waste_percent must be in [0, 100)", pointer="/waste_percent")


def create_template(uow: UnitOfWork, company_id: uuid.UUID, data: TemplateInput) -> PortioningTemplate:
    uow.ctx.require_company(company_id)
    _check_template(data)
    item = _item(uow, company_id, data.input_item_id, "")
    tpl = PortioningTemplate.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=data.code.strip(),
        name=data.name.strip()[:200],
        input_item=item,
        tolerance_percent=data.tolerance_percent,
        waste_percent=data.waste_percent,
        notes=data.notes[:2000],
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    _template_outputs(uow, company_id, tpl, data.outputs)
    audit(uow, "create", "inventory.portioning_template", tpl, {"code": tpl.code})
    return tpl


def update_template(uow: UnitOfWork, tpl: PortioningTemplate, data: TemplateInput) -> PortioningTemplate:
    tpl = PortioningTemplate.objects.select_for_update().get(id=tpl.id)
    _check_template(data)
    tpl.code, tpl.name = data.code.strip(), data.name.strip()[:200]
    tpl.input_item = _item(uow, tpl.company_id, data.input_item_id, "")
    tpl.tolerance_percent, tpl.waste_percent, tpl.notes = data.tolerance_percent, data.waste_percent, data.notes
    tpl.save_versioned(
        uow.ctx.principal_id, ["code", "name", "input_item", "tolerance_percent", "waste_percent", "notes"]
    )
    _template_outputs(uow, tpl.company_id, tpl, data.outputs)
    audit(uow, "update", "inventory.portioning_template", tpl)
    return tpl


def delete_template(uow: UnitOfWork, tpl: PortioningTemplate) -> None:
    if TransformationOrder.objects.filter(template_id=tpl.id).exists():
        raise Conflict("the template is used by transformations; archive it instead", code="invalid_state_transition")
    audit(uow, "delete", "inventory.portioning_template", tpl, {"code": tpl.code})
    PortioningTemplateOutput.objects.filter(template_id=tpl.id).delete()
    PortioningTemplate.objects.filter(id=tpl.id).delete()


# --------------------------------------------------------------------------- repack rules (derived)

RULE_NS = uuid.UUID("6f1f2a5e-7b0c-4c38-9a51-0d3c1f9e2b77")


@dataclass(frozen=True, slots=True)
class RepackRule:
    id: uuid.UUID
    tenant_id: uuid.UUID
    company_id: uuid.UUID
    item_id: uuid.UUID
    from_presentation_id: uuid.UUID | None
    to_presentation_id: uuid.UUID | None
    from_factor: Decimal
    to_factor: Decimal
    status: str = "ACTIVE"
    version: int = 1


def repack_rules(company_id: uuid.UUID, item_id: uuid.UUID | None = None) -> list[RepackRule]:
    """Derived from the presentation factors (D-013): base ↔ presentation, and presentation → presentation when the
    bigger factor is an exact multiple of the smaller one."""
    qs = Presentation.objects.filter(company_id=company_id, status=Status.ACTIVE).order_by("item_id", "factor", "id")
    if item_id is not None:
        qs = qs.filter(item_id=item_id)
    by_item: dict[uuid.UUID, list[Presentation]] = defaultdict(list)
    for p in qs:
        by_item[p.item_id].append(p)
    out: list[RepackRule] = []

    def rule(p_from: Presentation | None, p_to: Presentation | None, anchor: Presentation) -> RepackRule:
        key = f"{anchor.item_id}:{p_from.id if p_from else 'base'}:{p_to.id if p_to else 'base'}"
        return RepackRule(
            uuid.uuid5(RULE_NS, key),
            anchor.tenant_id,
            anchor.company_id,
            anchor.item_id,
            p_from.id if p_from else None,
            p_to.id if p_to else None,
            p_from.factor if p_from else Decimal(1),
            p_to.factor if p_to else Decimal(1),
        )

    for presentations in by_item.values():
        for p in presentations:
            out.append(rule(p, None, p))  # unpack
            out.append(rule(None, p, p))  # pack
        for a in presentations:
            for b in presentations:
                if (
                    a.id != b.id
                    and a.factor > b.factor
                    and (a.factor / b.factor) == (a.factor / b.factor).to_integral()
                ):
                    out.append(rule(a, b, a))
                    out.append(rule(b, a, a))
    return out


# --------------------------------------------------------------------------- create / update


@dataclass(slots=True)
class _Row:
    role: str
    quantity: Decimal
    item: Item | None = None
    presentation: Presentation | None = None
    location: Location | None = None
    lot: Lot | None = None
    lot_code: str = ""
    qty_packages: Decimal | None = None
    actual_weight: Decimal | None = None
    cost_share: Decimal | None = None
    is_main: bool = False
    is_byproduct: bool = False
    abnormal: bool = False


def _normalize(uow: UnitOfWork, company_id: uuid.UUID, wh: Warehouse, lines: tuple[LineInput, ...]) -> list[_Row]:
    rows: list[_Row] = []
    if len(lines) > 500:
        raise ValidationFailed("at most 500 lines", pointer="/lines", reason="max")
    for i, ln in enumerate(lines):
        ptr = f"/lines/{i}"
        if ln.role not in R.values:
            raise ValidationFailed("role must be INPUT, OUTPUT or WASTE", pointer=f"{ptr}/role", reason="enum")
        if ln.role == R.WASTE:
            waste_item = _item(uow, company_id, ln.item_id, ptr) if ln.item_id else None
            if ln.quantity is None or ln.quantity <= 0:
                raise ValidationFailed("waste quantity must be positive", pointer=f"{ptr}/quantity", reason="positive")
            if waste_item is not None:
                pres.check_precision(ln.quantity, waste_item.base_uom.precision)
            rows.append(_Row(R.WASTE, q_qty(ln.quantity), item=waste_item, abnormal=ln.abnormal))
            continue
        item = _item(uow, company_id, ln.item_id, ptr)
        presentation = _presentation(item, ln.presentation_id, ptr)
        qty = quantity_in_base(item, presentation, ln.quantity or ZERO, ln.qty_packages)
        if qty <= 0:
            raise ValidationFailed("quantity must be positive", pointer=f"{ptr}/quantity", reason="positive")
        if ln.actual_weight is not None and ln.actual_weight <= 0:
            raise ValidationFailed("actual_weight must be positive", pointer=f"{ptr}/actual_weight")
        if ln.cost_share is not None and not ZERO <= ln.cost_share <= HUNDRED:
            raise ValidationFailed("cost_share must be in [0, 100]", pointer=f"{ptr}/cost_share")
        if ln.role == R.INPUT and (ln.is_main or ln.is_byproduct or ln.lot_code):
            raise ValidationFailed("main/byproduct/lot_code apply to outputs", pointer=ptr, reason="role")
        rows.append(
            _Row(
                ln.role,
                qty,
                item=item,
                presentation=presentation,
                location=_location(company_id, wh, ln.location_id, ptr),
                lot=_lot(item, ln.lot_id, ptr),
                lot_code=ln.lot_code.strip()[:60],
                qty_packages=pres.to_packages(qty, presentation.factor, presentation.precision)
                if presentation
                else None,
                actual_weight=ln.actual_weight,
                cost_share=ln.cost_share,
                is_main=ln.is_main,
                is_byproduct=ln.is_byproduct,
            )
        )
    return rows


def _production_lines(bom: Bom, planned: Decimal, rows: list[_Row]) -> list[_Row]:
    """Fill INPUT lines (single level, scrap included) and main + byproduct OUTPUT lines from the BOM when the
    document gives none (explicit lines win: actual consumption / actual output)."""
    tree = bom_core.explode(boms.spec_of(bom), planned, lambda _i: None, multi_level=False)
    have_inputs = any(r.role == R.INPUT for r in rows)
    have_outputs = any(r.role == R.OUTPUT for r in rows)
    ids = [c.item_id for c in tree.children]
    items = {i.id: i for i in Item.objects.select_related("base_uom").filter(id__in=ids)}
    out = list(rows)
    shares = ZERO
    byproducts: list[_Row] = []
    for child in tree.children:
        item = items[child.item_id]
        qty = round_half_up(bom_core.to_decimal(child.qty), item.base_uom.precision)
        if child.is_byproduct:
            shares += child.cost_share_percent or ZERO
            if not have_outputs:
                row = _Row(R.OUTPUT, qty, item=item, is_byproduct=True, cost_share=child.cost_share_percent)
                byproducts.append(row)
                out.append(row)
        elif not have_inputs:
            out.append(_Row(R.INPUT, qty, item=item))
    if not have_outputs:
        main = Item.objects.select_related("base_uom").get(id=bom.item_id)
        p = bom.presentation
        out.append(
            _Row(
                R.OUTPUT,
                planned,
                item=main,
                presentation=p,
                qty_packages=pres.to_packages(planned, p.factor, p.precision) if p else None,
                is_main=True,
                cost_share=(HUNDRED - shares) if byproducts else None,
            )
        )
        for row in byproducts:
            row.cost_share = row.cost_share or ZERO
    return out


def _write_lines(uow: UnitOfWork, o: TransformationOrder, rows: list[_Row]) -> None:
    TransformationLine.objects.filter(order_id=o.id).delete()
    for n, r in enumerate(rows, start=1):
        TransformationLine.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=o.company_id,
            order=o,
            line_no=n,
            role=r.role,
            item=r.item,
            presentation=r.presentation,
            location=r.location,
            lot=r.lot,
            lot_code=r.lot_code,
            quantity=r.quantity,
            qty_packages=r.qty_packages,
            actual_weight=r.actual_weight,
            cost_share=r.cost_share,
            is_main=r.is_main,
            is_byproduct=r.is_byproduct,
            abnormal=r.abnormal,
        )


def _validate_shape(o: TransformationOrder, lines: list[TransformationLine]) -> None:
    inputs = [ln for ln in lines if ln.role == R.INPUT]
    outputs = [ln for ln in lines if ln.role == R.OUTPUT]
    if not outputs:
        raise ValidationFailed("at least one OUTPUT line is required", pointer="/lines", reason="required")
    if not inputs:
        raise error("transformation_no_inputs", "outputs need at least one input (no stock from nothing)")
    if sum(1 for ln in outputs if ln.is_main) > 1:
        raise ValidationFailed("only one main output", pointer="/lines", reason="main")
    if o.transformation_type == K.PRESENTATION:
        if len(inputs) != 1 or len(outputs) != 1 or any(ln.role == R.WASTE for ln in lines):
            raise ValidationFailed("a presentation change has exactly one input and one output", pointer="/lines")
        i, out = inputs[0], outputs[0]
        if i.item_id != out.item_id:
            raise ValidationFailed("repack keeps the item (use MANUAL for other items)", pointer="/lines")
        if i.quantity != out.quantity:
            raise error("transformation_unbalanced_value", "repack must keep the base quantity")
        if i.presentation_id == out.presentation_id:
            raise ValidationFailed("input and output presentations must differ", pointer="/lines")
        if out.location_id and i.location_id and out.location_id != i.location_id:
            raise ValidationFailed("repack happens in one bin", pointer="/lines")
    if o.transformation_type == K.PORTIONING:
        if len({ln.item_id for ln in inputs}) != 1:
            raise ValidationFailed("portioning consumes one input item", pointer="/lines")
        if o.template_id and inputs[0].item_id != o.template.input_item_id:  # type: ignore[union-attr]
            raise ValidationFailed("the input item is not the template's input item", pointer="/lines")


def create(uow: UnitOfWork, company_id: uuid.UUID, data: OrderInput) -> TransformationOrder:
    uow.ctx.require_company(company_id)
    if data.transformation_type not in K.values:
        raise ValidationFailed(
            "transformation_type must be PRESENTATION, PORTIONING, MANUAL or PRODUCTION",
            pointer="/transformation_type",
            reason="enum",
        )
    if data.additional_costs < 0:
        raise ValidationFailed("additional_costs must not be negative", pointer="/additional_costs")
    if data.tolerance_percent is not None and not ZERO <= data.tolerance_percent <= HUNDRED:
        raise ValidationFailed("tolerance_percent must be in [0, 100]", pointer="/tolerance_percent")
    wh = _warehouse(uow, company_id, data.warehouse_id)
    template = bom = None
    if data.template_id is not None:
        if data.transformation_type != K.PORTIONING:
            raise ValidationFailed("templates apply to PORTIONING", pointer="/template_id")
        template = PortioningTemplate.objects.filter(company_id=company_id, id=data.template_id).first()
        if template is None or template.status != Status.ACTIVE:
            raise ValidationFailed("unknown or archived template", pointer="/template_id", reason="not_found")
    if data.transformation_type == K.PRODUCTION:
        if data.bom_id is None or data.planned_qty is None or data.planned_qty <= 0:
            raise error("transformation_no_inputs", "a production order needs bom_id and planned_qty > 0")
        bom = Bom.objects.filter(company_id=company_id, id=data.bom_id).first()
        if bom is None:
            raise ValidationFailed("unknown BOM", pointer="/bom_id", reason="not_found")
        if bom.bom_status != Bom.BomStatus.ACTIVE or bom.bom_type != Bom.BomType.PRODUCTION:
            raise error("bom_component_inactive", "only an ACTIVE production BOM can be produced")
    elif data.bom_id is not None:
        raise ValidationFailed("bom_id applies to PRODUCTION", pointer="/bom_id")
    o = TransformationOrder.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        number=_number(uow, company_id, data.document_date),
        transformation_type=data.transformation_type,
        warehouse=wh,
        template=template,
        bom=bom,
        planned_qty=data.planned_qty if bom else None,
        document_date=data.document_date,
        notes=data.notes[:10000],
        reason=data.reason[:500],
        additional_costs=q_amount(data.additional_costs),
        tolerance_percent=data.tolerance_percent,
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    rows = _normalize(uow, company_id, wh, data.lines)
    if bom is not None and data.planned_qty is not None:
        rows = _production_lines(bom, data.planned_qty, rows)
    _write_lines(uow, o, rows)
    _validate_shape(o, _lines(o))
    audit(uow, "create", "inventory.transformation", o, {"number": o.number, "type": o.transformation_type})
    return o


def update(uow: UnitOfWork, o: TransformationOrder, data: OrderInput) -> TransformationOrder:
    o = _lock(o)
    _require(o, S.DRAFT)
    if o.inputs_reserved:
        raise Conflict("release the reservations (cancel) before editing", code="invalid_state_transition")
    if data.transformation_type != o.transformation_type:
        raise ValidationFailed("transformation_type cannot change", pointer="/transformation_type")
    wh = _warehouse(uow, o.company_id, data.warehouse_id)
    o.warehouse, o.document_date, o.notes, o.reason = wh, data.document_date, data.notes[:10000], data.reason[:500]
    o.additional_costs, o.tolerance_percent = q_amount(data.additional_costs), data.tolerance_percent
    o.save_versioned(
        uow.ctx.principal_id, ["warehouse", "document_date", "notes", "reason", "additional_costs", "tolerance_percent"]
    )
    if data.lines:
        rows = _normalize(uow, o.company_id, wh, data.lines)
        if o.bom is not None and o.planned_qty is not None:
            rows = _production_lines(o.bom, o.planned_qty, rows)
        _write_lines(uow, o, rows)
        _validate_shape(o, _lines(o))
    audit(uow, "update", "inventory.transformation", o)
    return o


def delete(uow: UnitOfWork, o: TransformationOrder) -> None:
    o = _lock(o)
    _require(o, S.DRAFT)
    if o.inputs_reserved:
        raise Conflict("cancel the transformation to release its reservations", code="invalid_state_transition")
    audit(uow, "delete", "inventory.transformation", o, {"number": o.number})
    TransformationLine.objects.filter(order_id=o.id).delete()
    TransformationOrder.objects.filter(id=o.id).delete()


# --------------------------------------------------------------------------- costing plan


def _preview_input_values(company_id: uuid.UUID, lines: list[TransformationLine]) -> dict[int, Decimal]:
    """What issuing the inputs would take now (weighted average preview; FIFO previews at the average)."""
    item_ids = {ln.item_id for ln in lines if ln.role == R.INPUT}
    vals = {v.item_id: v for v in ItemValuation.objects.filter(company_id=company_id, item_id__in=item_ids)}
    pos = {k: val.Position(v.qty, v.value, v.avg_cost) for k, v in vals.items()}
    out: dict[int, Decimal] = {}
    for ln in lines:
        if ln.role != R.INPUT or ln.item_id is None:
            continue
        p = pos.get(ln.item_id)
        if p is None or p.qty <= 0:
            out[ln.line_no] = ZERO
            continue
        if ln.quantity <= p.qty:
            pos[ln.item_id], out[ln.line_no] = val.wac_issue(p, ln.quantity)
        else:  # more than on hand: preview at the average (the post will refuse it unless negatives are allowed)
            out[ln.line_no] = q_amount(ln.quantity * p.avg_cost)
    return out


def _tolerance(o: TransformationOrder) -> Decimal:
    if o.tolerance_percent is not None:
        return Decimal(o.tolerance_percent)
    if o.template is not None:
        return Decimal(o.template.tolerance_percent)
    return DEFAULT_TOLERANCE


def plan(o: TransformationOrder, lines: list[TransformationLine], input_values: dict[int, Decimal]) -> Plan:
    """Approval, waste, allocation and journal preview for ``input_values`` (actual at post, preview otherwise)."""
    p = Plan(additional_costs=o.additional_costs)
    inputs = [ln for ln in lines if ln.role == R.INPUT]
    outputs = [ln for ln in lines if ln.role == R.OUTPUT]
    wastes = [ln for ln in lines if ln.role == R.WASTE]
    p.input_values = {ln.line_no: input_values.get(ln.line_no, ZERO) for ln in inputs}
    p.value_in = q_amount(sum(p.input_values.values(), ZERO))
    p.qty_in = q_qty(sum((ln.quantity for ln in inputs), ZERO))
    p.qty_out = q_qty(sum((_weight(ln) for ln in outputs), ZERO))
    waste_qty = q_qty(sum((ln.quantity for ln in wastes), ZERO))
    tol = _tolerance(o)
    kind = o.transformation_type

    if kind == K.PRESENTATION:
        p.output_values = {ln.line_no: ZERO for ln in outputs}
        return p

    same_uom = len({ln.item.base_uom.code for ln in [*inputs, *outputs] if ln.item is not None}) == 1
    if (kind == K.PORTIONING or (kind == K.MANUAL and same_uom)) and p.qty_out + waste_qty != p.qty_in:
        raise error(
            "transformation_unbalanced_value",
            f"base quantities do not balance: inputs {p.qty_in} ≠ outputs {p.qty_out} + waste {waste_qty}",
            reason="quantity",
        )

    # abnormal waste (expensed) vs normal waste (absorbed); valued at the unit value of the inputs of the same item
    def unit_basis(item_id: uuid.UUID | None) -> tuple[Decimal, Decimal]:
        same = [ln for ln in inputs if item_id is not None and ln.item_id == item_id] or inputs
        return (
            sum((p.input_values[ln.line_no] for ln in same), ZERO),
            sum((ln.quantity for ln in same), ZERO),
        )

    for ln in wastes:
        if ln.abnormal:
            v, q = unit_basis(ln.item_id)
            p.waste_values[ln.line_no] = core.waste_value(ln.quantity, v, q)
            p.abnormal_waste_qty += ln.quantity
    if o.template is not None:
        allowance = round_half_up(p.qty_in * (o.template.waste_percent + tol) / HUNDRED, 6)
        normal = [ln for ln in wastes if not ln.abnormal]
        excess = max(ZERO, sum((ln.quantity for ln in normal), ZERO) - allowance)
        if excess > 0 and normal:
            p.abnormal_waste_qty += excess
            p.waste_values[normal[0].line_no] = core.waste_value(excess, p.value_in, p.qty_in)
    p.abnormal_waste_qty = q_qty(p.abnormal_waste_qty)
    p.waste_expensed = q_amount(sum(p.waste_values.values(), ZERO))

    # approvals
    if o.template is not None:
        expected = {t.item_id: t for t in PortioningTemplateOutput.objects.filter(template_id=o.template_id)}
        per_item: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        for ln in outputs:
            assert ln.item_id is not None  # noqa: S101 - outputs always carry an item (DB check)
            per_item[ln.item_id] += _weight(ln)
        rows = [
            (n, t.expected_yield_percent, per_item.get(item_id, ZERO))
            for n, (item_id, t) in enumerate(expected.items())
        ]
        p.yield_checks = core.yield_checks(p.qty_in, rows, tol)
        codes = {t.item_id: t.item.code for t in expected.values()} if expected else {}
        names = list(expected)
        for chk in p.yield_checks:
            if not chk.within:
                p.approval_reasons.append(
                    f"rendimiento {codes.get(names[chk.key], '')} {chk.actual} % fuera de {chk.expected} ± {tol} %"
                )
        extra = [ln for ln in outputs if ln.item_id not in expected]
        if extra:
            p.approval_reasons.append("salidas fuera de la plantilla")
    if kind == K.PRODUCTION and o.planned_qty:
        main = [ln for ln in outputs if ln.is_main]
        if main:
            dev = abs(_weight(main[0]) - o.planned_qty) * HUNDRED / o.planned_qty
            if dev > tol:
                p.approval_reasons.append(f"producción real se desvía {round_half_up(dev, 2)} % de lo planeado")
    if kind == K.MANUAL and p.value_in + p.additional_costs > MANUAL_APPROVAL_THRESHOLD:
        p.approval_reasons.append(
            f"valor {p.value_in + p.additional_costs} supera el umbral {MANUAL_APPROVAL_THRESHOLD}"
        )
    p.approval_required = bool(p.approval_reasons)

    # allocation (D-016)
    template_out = (
        {t.item_id: t for t in PortioningTemplateOutput.objects.filter(template_id=o.template_id)} if o.template else {}
    )
    specs: list[core.OutputSpec] = []
    for ln in outputs:
        assert ln.item is not None  # noqa: S101 - outputs always carry an item (DB check)
        tpl = template_out.get(ln.item_id) if ln.item_id else None
        share = ln.cost_share if ln.cost_share is not None else (tpl.cost_share_percent if tpl else None)
        specs.append(
            core.OutputSpec(
                key=ln.line_no,
                weight_qty=_weight(ln),
                cost_share=share,
                standard_cost=ln.item.standard_cost,
                is_main=ln.is_main or bool(tpl and tpl.is_main),
            )
        )
    distributable = q_amount(p.value_in + p.additional_costs - p.waste_expensed)
    try:
        parts, p.allocation_basis = core.allocate(distributable, specs)
    except core.Unbalanced as exc:
        raise error("transformation_unbalanced_value", str(exc)) from None
    p.output_values = {s.key: v for s, v in zip(specs, parts, strict=True)}
    p.value_out = q_amount(sum(parts, ZERO))
    if not p.balanced:
        raise error("transformation_unbalanced_value", "value in ≠ value out + expensed waste")
    return p


# --------------------------------------------------------------------------- GL


_ROLE_CACHE: dict[tuple[uuid.UUID, str], str] = {}


def inventory_role(item: Item) -> str:
    """``INVENTORY_<CATEGORY>`` when the company maps that role (category valuation account), else ``INVENTORY``."""
    if not item.category:
        return INVENTORY_ROLE
    candidate = INVENTORY_ROLE + "_" + re.sub(r"[^A-Z0-9]+", "_", item.category.upper()).strip("_")
    from ledger.models import AccountDetermination

    mapped = AccountDetermination.objects.filter(company_id=item.company_id, role_code=candidate, active=True).exists()
    return candidate if mapped else INVENTORY_ROLE


def _journal(
    inv_lines: Iterable[tuple[Item, Decimal]], other: Iterable[tuple[str, Literal["DEBIT", "CREDIT"], Decimal]]
) -> list[JournalPreview]:
    """Net inventory movements per role (signed: + debit, − credit) and append the non-inventory lines."""
    net: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for item, signed in inv_lines:
        net[inventory_role(item)] += signed
    out = [
        JournalPreview(role, "DEBIT" if amount > 0 else "CREDIT", q_amount(abs(amount)))
        for role, amount in sorted(net.items())
        if q_amount(amount) != 0
    ]
    out += [JournalPreview(role, side, q_amount(amount)) for role, side, amount in other if q_amount(amount) != 0]
    return out


def _post_journal(
    uow: UnitOfWork, o: TransformationOrder, purpose: str, day: dt.date, journal: list[JournalPreview], doc: DocumentRef
) -> uuid.UUID | None:
    if not journal:
        return None
    memo = f"Transformación {o.number}"
    result = services.get(PostingService).post(
        uow,
        PostingRequest(
            company_id=o.company_id,
            posting_date=day,
            source=doc,
            purpose=purpose,
            lines=tuple(
                PostingLine(j.account_role, j.side, j.amount, warehouse_id=o.warehouse_id, memo=memo) for j in journal
            ),
            journal_code="INVENTORY",
            branch_id=o.warehouse.branch_id,
        ),
    )
    return result.entry_id


# --------------------------------------------------------------------------- commands


def _lock_valuations(uow: UnitOfWork, company_id: uuid.UUID, item_ids: Iterable[uuid.UUID | None]) -> None:
    for item_id in sorted({i for i in item_ids if i is not None}):
        item = Item.objects.get(company_id=company_id, id=item_id)
        ItemValuation.objects.select_for_update().get_or_create(
            tenant_id=uow.ctx.tenant_id,
            company_id=company_id,
            item_id=item_id,
            defaults={"method": item.valuation_method},
        )


def _active_reservations(o: TransformationOrder) -> list[Reservation]:
    return list(
        Reservation.objects.filter(
            company_id=o.company_id, source_type=SOURCE_TYPE, source_id=o.id, state=Reservation.State.ACTIVE
        ).order_by("line_ref")
    )


def _belongs(line_ref: str, prefix: str) -> bool:
    return line_ref == prefix or line_ref.startswith(prefix + "#")


def _of_input(line_ref: str, line_no: int) -> bool:
    """Issue moves of input line ``line_no``: ``I<n>``, ``I<n>#k`` (split bins) and ``I<n>+`` (unreserved rest)."""
    prefix = f"I{line_no}"
    return _belongs(line_ref, prefix) or _belongs(line_ref, prefix + "+")


def reserve(uow: UnitOfWork, o: TransformationOrder) -> TransformationOrder:
    o = _lock(o)
    _require(o, S.DRAFT, S.SUBMITTED, S.APPROVED)
    if o.inputs_reserved:
        return o
    lines = _lines(o)
    inputs = [ln for ln in lines if ln.role == R.INPUT]
    _lock_valuations(uow, o.company_id, (ln.item_id for ln in inputs))
    for ln in inputs:
        assert ln.item_id is not None  # noqa: S101 - inputs always carry an item
        ids = STOCK.reserve(
            uow,
            o.company_id,
            _doc(o),
            [
                StockLine(
                    line_ref=f"I{ln.line_no}",
                    item_id=ln.item_id,
                    warehouse_id=o.warehouse_id,
                    quantity=ln.quantity,
                    location_id=ln.location_id,
                    lot_id=ln.lot_id,
                    presentation_id=ln.presentation_id,
                )
            ],
        )
        res = list(Reservation.objects.filter(id__in=ids))
        if len(res) == 1 and (ln.location_id is None or ln.lot_id is None):
            ln.location_id, ln.lot_id = res[0].location_id, res[0].lot_id
            ln.save(update_fields=["location", "lot"])
    o.inputs_reserved = True
    o.save_versioned(uow.ctx.principal_id, ["inputs_reserved"])
    audit(uow, "reserve", "inventory.transformation", o)
    return o


def simulate(o: TransformationOrder) -> Plan:
    o = TransformationOrder.objects.select_related("warehouse", "template", "bom").get(id=o.id)
    lines = _lines(o)
    _validate_shape(o, lines)
    p = plan(o, lines, _preview_input_values(o.company_id, lines))
    p.journal = _journal_for_plan(o, lines, p)
    return p


def _journal_for_plan(o: TransformationOrder, lines: list[TransformationLine], p: Plan) -> list[JournalPreview]:
    inv: list[tuple[Item, Decimal]] = []
    for ln in lines:
        if ln.item is None or ln.role == R.WASTE:
            continue
        if ln.role == R.INPUT:
            inv.append((ln.item, -p.input_values.get(ln.line_no, ZERO)))
        else:
            inv.append((ln.item, p.output_values.get(ln.line_no, ZERO)))
    other: list[tuple[str, Literal["DEBIT", "CREDIT"], Decimal]] = []
    if p.waste_expensed:
        other.append((WASTE_ROLE, "DEBIT", p.waste_expensed))
    if p.additional_costs:
        other.append((CLEARING_ROLE, "CREDIT", p.additional_costs))
    return _journal(inv, other)


def submit(uow: UnitOfWork, o: TransformationOrder) -> TransformationOrder:
    """DRAFT → SUBMITTED when an approval is needed (tolerance / threshold), else → APPROVED."""
    o = _lock(o)
    _require(o, S.DRAFT)
    lines = _lines(o)
    _validate_shape(o, lines)
    p = plan(o, lines, _preview_input_values(o.company_id, lines))
    o.approval_required = p.approval_required
    o.approval_reasons = "; ".join(p.approval_reasons)[:2000]
    o.status = S.SUBMITTED if p.approval_required else S.APPROVED
    o.save_versioned(uow.ctx.principal_id, ["approval_required", "approval_reasons", "status"])
    audit(uow, "submit", "inventory.transformation", o, {"approval_required": p.approval_required})
    return o


def approve(uow: UnitOfWork, o: TransformationOrder, note: str = "") -> TransformationOrder:
    o = _lock(o)
    _require(o, S.SUBMITTED)
    if o.created_by is not None and o.created_by == uow.ctx.principal_id:
        raise Conflict("the requester cannot approve the transformation (segregation of duties)", code="sod_violation")
    if o.approval_required and not note.strip():
        raise ValidationFailed("an approval outside tolerance needs a reason (D-015)", pointer="/reason")
    o.status = S.APPROVED
    o.approved_by = uow.ctx.principal_id
    o.approved_at = clock.now()
    o.approval_note = note.strip()[:500]
    o.save_versioned(uow.ctx.principal_id, ["status", "approved_by", "approved_at", "approval_note"])
    audit(uow, "approve", "inventory.transformation", o, {"note": o.approval_note})
    return o


def reject(uow: UnitOfWork, o: TransformationOrder, note: str = "") -> TransformationOrder:
    o = _lock(o)
    _require(o, S.SUBMITTED)
    STOCK.release(uow, [r.id for r in _active_reservations(o)])
    o.status = S.REJECTED
    o.inputs_reserved = False
    o.approval_note = note.strip()[:500]
    o.save_versioned(uow.ctx.principal_id, ["status", "inputs_reserved", "approval_note"])
    audit(uow, "reject", "inventory.transformation", o)
    return o


def cancel(uow: UnitOfWork, o: TransformationOrder) -> TransformationOrder:
    o = _lock(o)
    _require(o, S.DRAFT, S.SUBMITTED, S.APPROVED)
    STOCK.release(uow, [r.id for r in _active_reservations(o)])
    o.status = S.CANCELLED
    o.inputs_reserved = False
    o.save_versioned(uow.ctx.principal_id, ["status", "inputs_reserved"])
    audit(uow, "cancel", "inventory.transformation", o)
    return o


def _repack_bin(o: TransformationOrder, ln: TransformationLine) -> Location:
    """Bin for a repack without one: a bin holding enough unreserved packages (unpack) or loose stock (pack)."""
    if ln.location is not None:
        return ln.location
    assert ln.item is not None  # noqa: S101 - inputs always carry an item
    if ln.presentation is not None:
        need = ln.qty_packages or ZERO
        pq = (
            PackagedQuant.objects.filter(
                presentation_id=ln.presentation.id,
                warehouse_id=o.warehouse_id,
                location__status=Status.ACTIVE,
                qty_packages__gte=F("reserved_packages") + need,
            )
            .select_related("location")
            .order_by("location__path", "lot_id")
            .first()
        )
        if pq is None:
            raise error("stock_insufficient", f"no bin of {o.warehouse.code} holds {need} × {ln.presentation.code}")
        return pq.location
    for q in (
        StockQuant.objects.filter(
            item_id=ln.item.id, warehouse_id=o.warehouse_id, location__location_type=Location.LocationType.INTERNAL
        )
        .select_related("location")
        .order_by("location__path")
    ):
        rows = PackagedQuant.objects.filter(item_id=ln.item.id, location_id=q.location_id).select_related(
            "presentation"
        )
        loose = pres.loose(q.on_hand, [pres.Packaged(r.id, r.qty_packages, r.presentation.factor) for r in rows])
        if loose >= ln.quantity:
            return q.location
    raise error("stock_insufficient", f"no bin of {o.warehouse.code} holds {ln.quantity} loose {ln.item.code}")


def _repack(uow: UnitOfWork, o: TransformationOrder, lines: list[TransformationLine], forward: bool) -> None:
    src = next(ln for ln in lines if ln.role == R.INPUT)
    dst = next(ln for ln in lines if ln.role == R.OUTPUT)
    assert src.item_id is not None  # noqa: S101 - inputs always carry an item
    loc = _repack_bin(o, src) if forward else (src.location or dst.location)
    assert loc is not None  # noqa: S101 - the bin is recorded at post
    first, second = (src, dst) if forward else (dst, src)
    if first.presentation_id is not None:  # open the packages of the source presentation
        STOCK.repack(
            uow,
            o.company_id,
            item_id=src.item_id,
            location_id=loc.id,
            presentation_id=first.presentation_id,
            packages=first.qty_packages or ZERO,
            pack=False,
            lot_id=src.lot_id,
        )
    if second.presentation_id is not None:  # fill packages of the target presentation
        STOCK.repack(
            uow,
            o.company_id,
            item_id=src.item_id,
            location_id=loc.id,
            presentation_id=second.presentation_id,
            packages=second.qty_packages or ZERO,
            pack=True,
            lot_id=src.lot_id,
        )
    if forward:
        for ln in (src, dst):
            ln.location = loc
            ln.unit_cost, ln.value = ZERO, ZERO
            ln.save(update_fields=["location", "unit_cost", "value"])


def _output_lot(
    uow: UnitOfWork, o: TransformationOrder, ln: TransformationLine, inputs: list[TransformationLine]
) -> Lot | None:
    assert ln.item is not None  # noqa: S101 - outputs always carry an item
    if ln.lot is not None:
        return ln.lot
    if ln.item.tracking != Item.Tracking.LOT:
        return None
    same = [i.lot for i in inputs if i.item_id == ln.item_id and i.lot is not None]
    if not ln.lot_code and len({lot.id for lot in same}) == 1:
        return same[0]
    code = ln.lot_code or f"{o.number}-{ln.line_no}"
    lot = Lot.objects.filter(company_id=o.company_id, item_id=ln.item.id, code__iexact=code).first()
    if lot is None:
        lot = Lot.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=o.company_id,
            item=ln.item,
            code=code[:60],
            created_by=uow.ctx.principal_id,
            updated_by=uow.ctx.principal_id,
        )
    return lot


def post(uow: UnitOfWork, o: TransformationOrder, day: dt.date | None = None) -> TransformationOrder:
    """Issue inputs, receive outputs, post the entry — one transaction (see module docstring)."""
    o = _lock(o)
    _require(o, S.DRAFT, S.APPROVED)
    day = day or o.document_date
    lines = _lines(o)
    _validate_shape(o, lines)
    preview = plan(o, lines, _preview_input_values(o.company_id, lines))
    if preview.approval_required and o.status != S.APPROVED:
        if o.transformation_type == K.MANUAL:
            raise Conflict(
                "this manual transformation needs approval: " + "; ".join(preview.approval_reasons),
                code="approval_required",
            )
        raise error(
            "transformation_yield_out_of_tolerance",
            "approval required: " + "; ".join(preview.approval_reasons),
            reasons=preview.approval_reasons,
        )
    _lock_valuations(uow, o.company_id, (ln.item_id for ln in lines))
    doc = _doc(o)
    inputs = [ln for ln in lines if ln.role == R.INPUT]
    outputs = [ln for ln in lines if ln.role == R.OUTPUT]

    if o.transformation_type == K.PRESENTATION:
        STOCK.release(uow, [r.id for r in _active_reservations(o)])
        _repack(uow, o, lines, forward=True)
        p = preview
        o.journal_entry_id = None
    else:
        reservations = _active_reservations(o)
        stock_in: list[StockLine] = []
        for ln in inputs:
            assert ln.item_id is not None  # noqa: S101 - inputs always carry an item
            own = [r for r in reservations if _belongs(r.line_ref, f"I{ln.line_no}")]
            if own:
                stock_in += [
                    StockLine(
                        r.line_ref, ln.item_id, o.warehouse_id, r.quantity, r.location_id, r.lot_id, r.presentation_id
                    )
                    for r in own
                ]
                left = ln.quantity - sum((r.quantity for r in own), ZERO)
                if left > 0:
                    stock_in.append(
                        StockLine(f"I{ln.line_no}+", ln.item_id, o.warehouse_id, left, ln.location_id, ln.lot_id)
                    )
            else:
                stock_in.append(
                    StockLine(
                        f"I{ln.line_no}",
                        ln.item_id,
                        o.warehouse_id,
                        ln.quantity,
                        ln.location_id,
                        ln.lot_id,
                        ln.presentation_id,
                    )
                )
        issued = STOCK.issue(uow, o.company_id, doc, stock_in, day)
        moves_in = list(StockMove.objects.filter(id__in=issued.move_ids))
        input_values = {
            ln.line_no: q_amount(sum((abs(m.value) for m in moves_in if _of_input(m.line_ref, ln.line_no)), ZERO))
            for ln in inputs
        }
        p = plan(o, lines, input_values)
        stock_out: list[StockLine] = []
        for ln in outputs:
            assert ln.item_id is not None and ln.item is not None  # noqa: S101 - outputs always carry an item
            value = p.output_values[ln.line_no]
            lot = _output_lot(uow, o, ln, inputs)
            if lot is not None and ln.lot_id != lot.id:
                ln.lot = lot
            try:
                pieces = core.receipt_split(ln.quantity, value)
            except core.Unbalanced as exc:
                raise error("transformation_unbalanced_value", str(exc)) from None
            if len(pieces) > 1 and ln.presentation_id is not None:
                raise error("transformation_unbalanced_value", "a packaged output cannot carry this value exactly")
            for k, (qty, unit, _v) in enumerate(pieces):
                stock_out.append(
                    StockLine(
                        f"O{ln.line_no}" + ("" if k == 0 else "r"),
                        ln.item_id,
                        o.warehouse_id,
                        qty,
                        ln.location_id,
                        lot.id if lot else None,
                        ln.presentation_id,
                        unit,
                    )
                )
        received = STOCK.receive(uow, o.company_id, doc, stock_out, day)
        moves_out = list(StockMove.objects.filter(id__in=received.move_ids))
        for ln in outputs:
            got = q_amount(
                sum((m.value for m in moves_out if m.line_ref in (f"O{ln.line_no}", f"O{ln.line_no}r")), ZERO)
            )
            if got != p.output_values[ln.line_no]:
                raise error("transformation_unbalanced_value", f"output line {ln.line_no} received {got}")
            if ln.location_id is None:
                first = next(m for m in moves_out if m.line_ref == f"O{ln.line_no}")
                ln.location_id = first.to_location_id
        for ln in inputs:
            mv = [m for m in moves_in if _of_input(m.line_ref, ln.line_no)]
            if len(mv) == 1 and ln.location_id is None:
                ln.location_id, ln.lot_id = mv[0].from_location_id, mv[0].lot_id
        p.journal = _journal_for_plan(o, lines, p)
        o.journal_entry_id = _post_journal(uow, o, "post", day, p.journal, doc)
        for ln in lines:
            if ln.role == R.INPUT:
                ln.value = p.input_values[ln.line_no]
            elif ln.role == R.OUTPUT:
                ln.value = p.output_values[ln.line_no]
            else:
                ln.value = p.waste_values.get(ln.line_no, ZERO)
            ln.unit_cost = val.average_of(ln.quantity, ln.value or ZERO)
            ln.save(update_fields=["value", "unit_cost", "location", "lot"])
        STOCK.release(uow, [r.id for r in _active_reservations(o)])  # leftovers (none when fully consumed)
    o.value_in, o.value_out, o.waste_expensed = p.value_in, p.value_out, p.waste_expensed
    o.approval_required = preview.approval_required
    o.approval_reasons = "; ".join(preview.approval_reasons)[:2000]
    o.posting_date = day
    o.posted_at = clock.now()
    o.status = S.POSTED
    o.inputs_reserved = False
    o.save_versioned(
        uow.ctx.principal_id,
        [
            "value_in",
            "value_out",
            "waste_expensed",
            "approval_required",
            "approval_reasons",
            "posting_date",
            "posted_at",
            "status",
            "inputs_reserved",
            "journal_entry_id",
        ],
    )
    audit(uow, "post", "inventory.transformation", o, {"value_in": format(p.value_in, "f")})
    _event(uow, o)
    return o


def reverse(uow: UnitOfWork, o: TransformationOrder, reason: str, day: dt.date | None = None) -> TransformationOrder:
    """Reversal document: outputs leave at their current cost, inputs return at their original cost and bin; the
    entry mirrors the moves, a WAC difference on the outputs goes to INVENTORY_ADJ_GAIN/LOSS (never deletion)."""
    o = _lock(o)
    _require(o, S.POSTED)
    if not reason.strip():
        raise ValidationFailed("a reversal needs a reason", pointer="/reason", reason="required")
    day = day or clock.business_date()
    lines = _lines(o)
    _lock_valuations(uow, o.company_id, (ln.item_id for ln in lines))
    if o.transformation_type == K.PRESENTATION:
        _repack(uow, o, lines, forward=False)
    else:
        rdoc = DocumentRef(REVERSAL_SOURCE_TYPE, o.id, o.number or None)
        returned = STOCK.return_moves(uow, o.company_id, rdoc, _doc(o))
        moves = list(StockMove.objects.filter(id__in=returned.move_ids).select_related("item", "original_move"))
        inv: list[tuple[Item, Decimal]] = [(m.item, m.value) for m in moves]
        out_now = sum((-m.value for m in moves if m.value < 0), ZERO)
        variance = q_amount(out_now - (o.value_out or ZERO))
        other: list[tuple[str, Literal["DEBIT", "CREDIT"], Decimal]] = []
        if o.waste_expensed:
            other.append((WASTE_ROLE, "CREDIT", o.waste_expensed))
        if o.additional_costs:
            other.append((CLEARING_ROLE, "DEBIT", o.additional_costs))
        if variance > 0:
            other.append((LOSS_ROLE, "DEBIT", variance))
        elif variance < 0:
            other.append((GAIN_ROLE, "CREDIT", -variance))
        o.reversal_entry_id = _post_journal(uow, o, "reverse", day, _journal(inv, other), rdoc)
    o.status = S.REVERSED
    o.reversed_at = clock.now()
    o.approval_note = (o.approval_note + f" | reversa: {reason.strip()}")[:500]
    o.save_versioned(uow.ctx.principal_id, ["status", "reversed_at", "reversal_entry_id", "approval_note"])
    audit(uow, "reverse", "inventory.transformation", o, {"reason": reason.strip()[:200]})
    return o
