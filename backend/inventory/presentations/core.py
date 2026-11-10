"""Pure presentation math (F-INV-PRES, D-013). Shared by the service and the fakes.

* A presentation factor is an exact positive Decimal with at most 6 decimals (NUMERIC(20,6)).
* ``to_base(packages)`` = packages × factor, exact; the result must fit the base UoM precision.
* ``to_packages(base)`` = base / factor; it must be exact at the presentation precision —
  fractional packages (``3.5`` boxes with precision 0) are rejected with ``uom_precision_violation``.
* Packaged stock is a sub-ledger of the canonical quant: Σ(packages × factor) ≤ canonical base qty;
  ``loose`` is the remainder. Nothing is ever counted twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

from inventory.errors import error

MAX_PLACES = 6
ZERO = Decimal(0)


def _places(value: Decimal) -> int:
    exp = value.normalize().as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else MAX_PLACES + 1


def parse_decimal(value: object, code: str = "presentation_factor_invalid") -> Decimal:
    """Strings / ints / Decimals only (never float)."""
    if isinstance(value, (float, bool)) or value is None:
        raise error(code, "decimal values are strings (never floats)")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise error(code, f"invalid decimal {value!r}") from None
    if not out.is_finite():
        raise error(code, "decimal must be finite")
    return out


def validate_factor(value: object) -> Decimal:
    factor = parse_decimal(value)
    if factor <= 0:
        raise error("presentation_factor_invalid", "the factor must be greater than zero")
    if _places(factor) > MAX_PLACES:
        raise error("presentation_factor_invalid", "the factor allows at most 6 decimals")
    if factor >= Decimal(10) ** 14:
        raise error("presentation_factor_invalid", "the factor is too large")
    return factor


def validate_precision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_PLACES:
        raise error("uom_precision_violation", "precision must be an integer between 0 and 6")
    return value


def check_precision(qty: Decimal, precision: int, what: str = "quantity") -> Decimal:
    """``qty`` must not carry more decimals than ``precision`` (no silent rounding)."""
    if _places(qty) > precision:
        raise error("uom_precision_violation", f"{what} {qty} exceeds precision {precision}")
    return qty


def to_base(packages: Decimal, factor: Decimal, presentation_precision: int, base_precision: int = MAX_PLACES) -> Decimal:
    check_precision(packages, presentation_precision, "package quantity")
    base = packages * factor
    check_precision(base, base_precision, "base quantity")
    return base


def to_packages(base: Decimal, factor: Decimal, presentation_precision: int) -> Decimal:
    if factor <= 0:
        raise error("presentation_factor_invalid", "the factor must be greater than zero")
    packages = base / factor
    if packages * factor != base or _places(packages) > presentation_precision:
        raise error(
            "uom_precision_violation",
            f"{base} base units are not a whole number of packages of {factor} (precision {presentation_precision})",
        )
    return packages


@dataclass(frozen=True, slots=True)
class Packaged:
    key: object
    packages: Decimal
    factor: Decimal
    reserved_packages: Decimal = ZERO

    @property
    def base(self) -> Decimal:
        return self.packages * self.factor


def packaged_base(packaged: Iterable[Packaged]) -> Decimal:
    return sum((p.base for p in packaged), ZERO)


def loose(canonical: Decimal, packaged: Iterable[Packaged]) -> Decimal:
    return canonical - packaged_base(packaged)


def check_invariant(canonical: Decimal, packaged: Sequence[Packaged]) -> None:
    if any(p.packages < 0 for p in packaged) or packaged_base(packaged) > canonical:
        raise error("packaged_exceeds_canonical", "packaged stock exceeds the canonical quantity")


def unpack_plan(canonical: Decimal, packaged: Sequence[Packaged], loose_needed: Decimal) -> list[tuple[object, Decimal]]:
    """Packages to open so that ``loose_needed`` base units are loose (smallest factor first, whole
    unreserved packages). Raises ``stock_insufficient`` if even opening every free package is not enough."""
    free_loose = loose(canonical, packaged)
    deficit = loose_needed - free_loose
    plan: list[tuple[object, Decimal]] = []
    if deficit <= 0:
        return plan
    for p in sorted(packaged, key=lambda x: (x.factor, str(x.key))):
        free = p.packages - p.reserved_packages
        if free <= 0:
            continue
        need = (deficit / p.factor).to_integral_value(rounding=ROUND_CEILING)
        take = min(free.to_integral_value(rounding=ROUND_FLOOR), need)
        if take <= 0:
            continue
        plan.append((p.key, take))
        deficit -= take * p.factor
        if deficit <= 0:
            return plan
    raise error("stock_insufficient", "not enough loose or unreserved packaged stock")
