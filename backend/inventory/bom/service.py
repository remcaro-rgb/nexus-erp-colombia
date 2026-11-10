"""BOM service (F-INV-BOM): versions, activation checks, explosion (+ costing). Runs in the caller's unit of work.

* ``create`` / ``copy_version`` → DRAFT with ``version_no = max(version_no of the code) + 1``.
* ``activate`` validates: output and components ACTIVE (``bom_component_inactive``), 0 < yield ≤ 100
  (``bom_yield_invalid``), no cycle across the ACTIVE versions of the company (``bom_cycle``) and no overlap of
  validity with another ACTIVE version of the same output item and type (``bom_version_overlap``). With
  ``supersede`` an older open-ended ACTIVE version is closed the day before (or made OBSOLETE when it would
  start on/after the new one). Concurrent activations lock every item of the BOM graph in id order first, so
  two activations that would close a cycle together serialize and the second one sees the first.
* ``explode`` is multi-level and exact (``inventory.bom.core``); ``costed`` values every leaf with the same
  function the stock service uses for an issue (``valuation.core.cost_preview`` on the item's valuation row),
  so the costed explosion equals what consuming those leaves would take from inventory (T-INV-BOM-02).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Max, Q
from django.db.models.functions import Lower

from inventory.bom import core
from inventory.bom.models import Bom, BomLine
from inventory.common import audit
from inventory.errors import error
from inventory.models import Item, ItemValuation, Presentation, Status
from inventory.valuation import core as val
from kernel import clock
from kernel.errors import Conflict, NotFound, ValidationFailed
from kernel.money import q_amount, q_unit_cost
from kernel.uow import UnitOfWork

ZERO = Decimal(0)
S = Bom.BomStatus


@dataclass(frozen=True, slots=True)
class LineInput:
    item_id: uuid.UUID
    quantity: Decimal
    scrap_percent: Decimal = ZERO
    is_byproduct: bool = False
    cost_share_percent: Decimal | None = None
    presentation_id: uuid.UUID | None = None
    is_optional: bool = False
    substitute_group: str = ""


@dataclass(frozen=True, slots=True)
class BomInput:
    code: str
    name: str
    item_id: uuid.UUID
    valid_from: dt.date
    output_qty: Decimal
    lines: tuple[LineInput, ...]
    valid_to: dt.date | None = None
    yield_percent: Decimal = Decimal(100)
    normal_waste_percent: Decimal = ZERO
    bom_type: str = Bom.BomType.PRODUCTION
    presentation_id: uuid.UUID | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CostedLeaf:
    item_id: uuid.UUID
    item_code: str
    quantity: Decimal
    unit_cost: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class Explosion:
    bom: Bom
    quantity: Decimal
    on_date: dt.date
    tree: core.Node
    leaves: tuple[CostedLeaf, ...] = field(default_factory=tuple)
    total_cost: Decimal = ZERO


# --------------------------------------------------------------------------- validation helpers


def _item(company_id: uuid.UUID, item_id: uuid.UUID, pointer: str) -> Item:
    item = Item.objects.select_related("base_uom").filter(company_id=company_id, id=item_id).first()
    if item is None:
        raise ValidationFailed("unknown item", pointer=pointer, reason="not_found")
    return item


def _presentation(item: Item, presentation_id: uuid.UUID | None, pointer: str) -> Presentation | None:
    if presentation_id is None:
        return None
    p = Presentation.objects.filter(company_id=item.company_id, item_id=item.id, id=presentation_id).first()
    if p is None:
        raise ValidationFailed("unknown presentation for this item", pointer=pointer, reason="not_found")
    return p


def _check_header(data: BomInput) -> None:
    if not data.code.strip() or len(data.code) > 40:
        raise ValidationFailed("code is required (≤ 40 characters)", pointer="/code", reason="required")
    if not data.name.strip():
        raise ValidationFailed("name is required", pointer="/name", reason="required")
    if data.output_qty <= 0:
        raise ValidationFailed("output_qty must be positive", pointer="/output_qty", reason="positive")
    if not ZERO < data.yield_percent <= 100:
        raise error("bom_yield_invalid", "yield_percent must be in (0, 100]")
    if not ZERO <= data.normal_waste_percent < 100:
        raise error("bom_yield_invalid", "normal_waste_percent must be in [0, 100)")
    if data.valid_to is not None and data.valid_to < data.valid_from:
        raise ValidationFailed("valid_to is before valid_from", pointer="/valid_to", reason="range")
    if data.bom_type not in Bom.BomType.values:
        raise ValidationFailed("bom_type must be KIT or PRODUCTION", pointer="/bom_type", reason="enum")
    if not data.lines or len(data.lines) > 500:
        raise ValidationFailed("1–500 component lines required", pointer="/components", reason="required")


def _next_version(company_id: uuid.UUID, code: str) -> int:
    top = (
        Bom.objects.filter(company_id=company_id)
        .annotate(lcode=Lower("code"))
        .filter(lcode=code.lower())
        .aggregate(m=Max("version_no"))["m"]
    )
    return int(top or 0) + 1


def _write_lines(uow: UnitOfWork, bom: Bom, output: Item, lines: tuple[LineInput, ...]) -> None:
    BomLine.objects.filter(bom_id=bom.id).delete()
    shares = ZERO
    components = 0
    for n, ln in enumerate(lines, start=1):
        ptr = f"/components/lines/{n - 1}"
        item = _item(bom.company_id, ln.item_id, f"{ptr}/item_id")
        if item.id == output.id and not ln.is_byproduct:
            raise error("bom_cycle", f"{output.code} cannot be a component of itself")
        if ln.quantity <= 0:
            raise ValidationFailed("quantity must be positive", pointer=f"{ptr}/quantity", reason="positive")
        if not ZERO <= ln.scrap_percent < 1000:
            raise ValidationFailed("scrap_percent must be in [0, 1000)", pointer=f"{ptr}/scrap_percent", reason="range")
        if ln.cost_share_percent is not None:
            if not ZERO <= ln.cost_share_percent <= 100 or not ln.is_byproduct:
                raise ValidationFailed(
                    "cost_share_percent applies to byproducts (0–100)", pointer=f"{ptr}/cost_share_percent"
                )
            shares += ln.cost_share_percent
        components += 0 if ln.is_byproduct else 1
        BomLine.objects.create(
            tenant_id=uow.ctx.tenant_id,
            company_id=bom.company_id,
            bom=bom,
            line_no=n,
            role=BomLine.Role.BYPRODUCT if ln.is_byproduct else BomLine.Role.COMPONENT,
            item=item,
            presentation=_presentation(item, ln.presentation_id, f"{ptr}/presentation_id"),
            quantity=ln.quantity,
            scrap_percent=ln.scrap_percent,
            cost_share_percent=ln.cost_share_percent,
            is_optional=ln.is_optional,
            substitute_group=ln.substitute_group[:40],
        )
    if components == 0:
        raise ValidationFailed("a BOM needs at least one component", pointer="/components", reason="required")
    if shares >= 100:
        raise ValidationFailed("byproduct cost shares must leave a share for the main output", pointer="/components")


# --------------------------------------------------------------------------- commands


def create(uow: UnitOfWork, company_id: uuid.UUID, data: BomInput) -> Bom:
    uow.ctx.require_company(company_id)
    _check_header(data)
    output = _item(company_id, data.item_id, "/item_id")
    bom = Bom.objects.create(
        tenant_id=uow.ctx.tenant_id,
        company_id=company_id,
        code=data.code.strip(),
        name=data.name.strip()[:200],
        bom_type=data.bom_type,
        item=output,
        presentation=_presentation(output, data.presentation_id, "/presentation_id"),
        version_no=_next_version(company_id, data.code.strip()),
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        output_qty=data.output_qty,
        yield_percent=data.yield_percent,
        normal_waste_percent=data.normal_waste_percent,
        notes=data.notes[:2000],
        created_by=uow.ctx.principal_id,
        updated_by=uow.ctx.principal_id,
    )
    _write_lines(uow, bom, output, data.lines)
    audit(uow, "create", "inventory.bom", bom, {"code": bom.code, "version_no": bom.version_no})
    return bom


def _lock(bom: Bom) -> Bom:
    locked = Bom.objects.select_for_update().select_related("item").filter(id=bom.id).first()
    if locked is None:
        raise NotFound("bom not found")
    return locked


def _require(bom: Bom, *allowed: str) -> None:
    if bom.bom_status not in allowed:
        raise Conflict(f"BOM is {bom.bom_status}; expected {' or '.join(allowed)}", code="invalid_state_transition")


def update(uow: UnitOfWork, bom: Bom, data: BomInput) -> Bom:
    bom = _lock(bom)
    _require(bom, S.DRAFT)
    _check_header(data)
    output = _item(bom.company_id, data.item_id, "/item_id")
    bom.name, bom.item, bom.bom_type = data.name.strip()[:200], output, data.bom_type
    bom.presentation = _presentation(output, data.presentation_id, "/presentation_id")
    bom.valid_from, bom.valid_to, bom.output_qty = data.valid_from, data.valid_to, data.output_qty
    bom.yield_percent, bom.normal_waste_percent, bom.notes = data.yield_percent, data.normal_waste_percent, data.notes
    bom.save_versioned(
        uow.ctx.principal_id,
        [
            "name",
            "item",
            "bom_type",
            "presentation",
            "valid_from",
            "valid_to",
            "output_qty",
            "yield_percent",
            "normal_waste_percent",
            "notes",
        ],
    )
    _write_lines(uow, bom, output, data.lines)
    audit(uow, "update", "inventory.bom", bom)
    return bom


def delete(uow: UnitOfWork, bom: Bom) -> None:
    bom = _lock(bom)
    _require(bom, S.DRAFT)
    audit(uow, "delete", "inventory.bom", bom, {"code": bom.code, "version_no": bom.version_no})
    BomLine.objects.filter(bom_id=bom.id).delete()
    Bom.objects.filter(id=bom.id).delete()


def _graph(company_id: uuid.UUID, exclude: uuid.UUID | None = None) -> dict[uuid.UUID, set[uuid.UUID]]:
    edges: dict[uuid.UUID, set[uuid.UUID]] = {}
    rows = BomLine.objects.filter(
        company_id=company_id, bom__bom_status=S.ACTIVE, role=BomLine.Role.COMPONENT
    ).values_list("bom__item_id", "item_id", "bom_id")
    for out, comp, bom_id in rows:
        if bom_id != exclude:
            edges.setdefault(out, set()).add(comp)
    return edges


def _lock_graph_items(company_id: uuid.UUID, bom: Bom, lines: list[BomLine]) -> None:
    ids = {bom.item_id, *(ln.item_id for ln in lines)}
    for out, comps in _graph(company_id).items():
        ids.add(out)
        ids.update(comps)
    list(Item.objects.select_for_update().filter(company_id=company_id, id__in=ids).order_by("id").values("id"))


def validate_for_activation(bom: Bom, lines: list[BomLine]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Activation report checks (raise the stable codes); returns the graph including ``bom``."""
    if not ZERO < bom.yield_percent <= 100:
        raise error("bom_yield_invalid", "yield_percent must be in (0, 100]")
    if bom.item.status != Status.ACTIVE:
        raise error("bom_component_inactive", f"output item {bom.item.code} is archived")
    inactive = [ln.item.code for ln in lines if ln.item.status != Status.ACTIVE]
    if inactive:
        raise error("bom_component_inactive", f"inactive components: {', '.join(sorted(inactive))}")
    graph = _graph(bom.company_id, exclude=bom.id)
    graph.setdefault(bom.item_id, set()).update(ln.item_id for ln in lines if ln.role == BomLine.Role.COMPONENT)
    cycle = core.find_cycle(graph, bom.item_id)
    if cycle is not None:
        codes = dict(Item.objects.filter(id__in=cycle).values_list("id", "code"))
        raise error("bom_cycle", "cycle across BOMs: " + " → ".join(codes.get(i, str(i)) for i in cycle))
    return graph


def activate(uow: UnitOfWork, bom: Bom, *, supersede: bool = False) -> Bom:
    bom = _lock(bom)
    _require(bom, S.DRAFT)
    lines = list(BomLine.objects.filter(bom_id=bom.id).select_related("item").order_by("line_no"))
    _lock_graph_items(bom.company_id, bom, lines)
    validate_for_activation(bom, lines)
    others = list(
        Bom.objects.select_for_update()
        .filter(company_id=bom.company_id, item_id=bom.item_id, bom_type=bom.bom_type, bom_status=S.ACTIVE)
        .exclude(id=bom.id)
        .order_by("valid_from", "id")
    )
    for other in others:
        if not core.overlaps(bom.valid_from, bom.valid_to, other.valid_from, other.valid_to):
            continue
        if supersede and other.valid_from < bom.valid_from and other.valid_to is None:
            other.valid_to = bom.valid_from - dt.timedelta(days=1)
            other.save_versioned(uow.ctx.principal_id, ["valid_to"])
            audit(uow, "supersede", "inventory.bom", other, {"valid_to": other.valid_to.isoformat()})
            continue
        raise error(
            "bom_version_overlap",
            f"version {other.version_no} of {other.code} is ACTIVE from {other.valid_from} to "
            f"{other.valid_to or '∞'}, overlapping {bom.valid_from} to {bom.valid_to or '∞'}",
        )
    bom.bom_status = S.ACTIVE
    bom.activated_at = clock.now()
    bom.activated_by = uow.ctx.principal_id
    bom.save_versioned(uow.ctx.principal_id, ["bom_status", "activated_at", "activated_by"])
    audit(uow, "activate", "inventory.bom", bom)
    uow.emit(
        "inventory.bom.activated.v1",
        {"bom_id": str(bom.id), "item_id": str(bom.item_id), "version_no": bom.version_no},
        company_id=bom.company_id,
    )
    return bom


def obsolete(uow: UnitOfWork, bom: Bom) -> Bom:
    bom = _lock(bom)
    _require(bom, S.ACTIVE, S.DRAFT)
    bom.bom_status = S.OBSOLETE
    bom.save_versioned(uow.ctx.principal_id, ["bom_status"])
    audit(uow, "obsolete", "inventory.bom", bom)
    return bom


def copy_lines(bom: Bom) -> tuple[LineInput, ...]:
    return tuple(
        LineInput(
            item_id=ln.item_id,
            quantity=ln.quantity,
            scrap_percent=ln.scrap_percent,
            is_byproduct=ln.role == BomLine.Role.BYPRODUCT,
            cost_share_percent=ln.cost_share_percent,
            presentation_id=ln.presentation_id,
            is_optional=ln.is_optional,
            substitute_group=ln.substitute_group,
        )
        for ln in BomLine.objects.filter(bom_id=bom.id).order_by("line_no")
    )


def copy_version(
    uow: UnitOfWork, bom: Bom, *, valid_from: dt.date | None = None, valid_to: dt.date | None = None
) -> Bom:
    src = Bom.objects.select_related("item").get(id=bom.id)
    start = valid_from or src.valid_from
    data = BomInput(
        code=src.code,
        name=src.name,
        item_id=src.item_id,
        valid_from=start,
        valid_to=valid_to,
        output_qty=src.output_qty,
        yield_percent=src.yield_percent,
        normal_waste_percent=src.normal_waste_percent,
        bom_type=src.bom_type,
        presentation_id=src.presentation_id,
        notes=src.notes,
        lines=copy_lines(src),
    )
    new = create(uow, src.company_id, data)
    audit(uow, "copy_version", "inventory.bom", new, {"from_version": src.version_no})
    return new


# --------------------------------------------------------------------------- queries


def spec_of(bom: Bom) -> core.BomSpec:
    lines = BomLine.objects.filter(bom_id=bom.id).order_by("line_no")
    return core.BomSpec(
        id=bom.id,
        item_id=bom.item_id,
        output_qty=bom.output_qty,
        yield_percent=bom.yield_percent,
        version_no=bom.version_no,
        code=bom.code,
        components=tuple(
            core.Component(
                item_id=ln.item_id,
                quantity=ln.quantity,
                scrap_percent=ln.scrap_percent,
                is_byproduct=ln.role == BomLine.Role.BYPRODUCT,
                cost_share_percent=ln.cost_share_percent,
                presentation_id=ln.presentation_id,
                is_optional=ln.is_optional,
                line_no=ln.line_no,
            )
            for ln in lines
        ),
    )


def active_bom(
    company_id: uuid.UUID, item_id: uuid.UUID, on_date: dt.date, bom_type: str = Bom.BomType.PRODUCTION
) -> Bom | None:
    """The ACTIVE version valid on ``on_date`` (at most one by the overlap invariant)."""
    return (
        Bom.objects.filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=on_date),
            company_id=company_id,
            item_id=item_id,
            bom_type=bom_type,
            bom_status=S.ACTIVE,
            valid_from__lte=on_date,
        )
        .order_by("-version_no")
        .first()
    )


def explode(bom: Bom, quantity: Decimal, on_date: dt.date, *, multi_level: bool = True) -> Explosion:
    """Exact multi-level explosion of ``bom`` for ``quantity`` good units, every leaf costed at valuation."""
    if quantity <= 0:
        raise ValidationFailed("qty must be positive", pointer="/qty", reason="positive")
    cache: dict[uuid.UUID, core.BomSpec | None] = {}

    def resolve(item_id: uuid.UUID) -> core.BomSpec | None:
        if item_id not in cache:
            sub = active_bom(bom.company_id, item_id, on_date)
            cache[item_id] = spec_of(sub) if sub is not None else None
        return cache[item_id]

    try:
        tree = core.explode(spec_of(bom), quantity, resolve, multi_level=multi_level)
    except core.BomCycle as exc:
        raise error("bom_cycle", str(exc)) from None
    leaves = core.leaves(tree)
    items = {i.id: i for i in Item.objects.filter(company_id=bom.company_id, id__in=[n.item_id for n in leaves])}
    vals = {v.item_id: v for v in ItemValuation.objects.filter(company_id=bom.company_id, item_id__in=items)}
    costed: list[CostedLeaf] = []
    for n in leaves:
        qty = n.quantity
        v = vals.get(n.item_id)
        if v is not None and v.qty > 0:
            value = val.cost_preview(val.Position(v.qty, v.value, v.avg_cost), qty)
        else:
            std = (v.avg_cost if v is not None and v.avg_cost else None) or items[n.item_id].standard_cost or ZERO
            value = q_amount(qty * std)
        costed.append(CostedLeaf(n.item_id, items[n.item_id].code, qty, val.average_of(qty, value), value))
    total = q_amount(sum((c.value for c in costed), ZERO))
    return Explosion(bom, quantity, on_date, tree, tuple(costed), total)


def unit_cost_of(explosion: Explosion) -> Decimal:
    return q_unit_cost(explosion.total_cost / explosion.quantity) if explosion.quantity > 0 else ZERO
