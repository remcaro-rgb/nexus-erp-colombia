"""Accounting-dimension slot registry (DIMENSIONS-ADDENDUM, ui/dimensions.json).

Each slot names the journal/document line field, the master model that P05
owns and whether references are company-scoped (composite FK with
``company_id``). P05/P17/P18 iterate :func:`slots` instead of hard-coding them.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_DEPTH = 6  # D-007
MIN_DEPTH, MAX_DEPTH = 1, 10


@dataclass(frozen=True, slots=True)
class DimensionSlot:
    key: str
    line_slot: str
    fk_target: str
    company_scoped: bool
    label_es: str
    api_path: str


_slots: dict[str, DimensionSlot] = {}


def register(slot: DimensionSlot) -> DimensionSlot:
    for other in _slots.values():
        if other.key != slot.key and other.line_slot == slot.line_slot:
            raise ValueError(f"line slot {slot.line_slot!r} already used by {other.key!r}")
    existing = _slots.get(slot.key)
    if existing is not None and existing != slot:
        raise ValueError(f"dimension {slot.key!r} already registered differently")
    _slots[slot.key] = slot
    return slot


def get(key: str) -> DimensionSlot:
    try:
        return _slots[key]
    except KeyError:
        raise KeyError(f"unknown dimension {key!r}") from None


def slots() -> list[DimensionSlot]:
    return sorted(_slots.values(), key=lambda s: s.key)


def line_slots() -> list[str]:
    return [s.line_slot for s in slots()]


def by_line_slot(line_slot: str) -> DimensionSlot:
    for s in _slots.values():
        if s.line_slot == line_slot:
            return s
    raise KeyError(f"unknown line slot {line_slot!r}")


def validate_depth(depth: int) -> int:
    if not MIN_DEPTH <= depth <= MAX_DEPTH:
        raise ValueError(f"dimension depth must be between {MIN_DEPTH} and {MAX_DEPTH}")
    return depth


for _slot in (
    DimensionSlot(
        "cost_center",
        "cost_center_id",
        "ledger.CostCenter",
        True,
        "Centro de costo",
        "/api/v1/ledger/dimensions/cost-centers",
    ),
    DimensionSlot(
        "business_unit",
        "business_unit_id",
        "ledger.BusinessUnit",
        True,
        "Unidad de negocio",
        "/api/v1/ledger/dimensions/business-units",
    ),
    DimensionSlot(
        "aux_abierto",
        "aux_abierto_id",
        "ledger.AuxAbierto",
        True,
        "Auxiliar abierto",
        "/api/v1/ledger/dimensions/aux-abierto",
    ),
    DimensionSlot(
        "item_contable",
        "item_dimension_id",
        "ledger.ItemContable",
        True,
        "Ítem contable",
        "/api/v1/ledger/dimensions/item-contable",
    ),
):
    register(_slot)
