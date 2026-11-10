"""Independent Decimal oracle for the deterministic seed scenarios S1-S12 (shared/11).

Pure standard library (``decimal``), no Django, no import of the system under test: every expected
value of a scenario is recomputed here from first principles (shared/07 §7 valuation algorithms,
§8 rounding policy) so a scenario test compares the system against an oracle that cannot share
its bugs. The published numbers of shared/11 are asserted against this oracle in
``tests/cross_module/scenarios/test_S00_golden_oracle.py``.

Rates marked FIX are FIXTURE values for engine mechanics, **NOT LEGALLY VERIFIED** (shared/11
header, C1-9). They are never used by production code: scenario tests hand them to the real
engines through a labelled synthetic parameter provider.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D(0)
CENT = D("0.01")
AMOUNT = D("0.0001")  # NUMERIC(20,4)
UNIT_COST = D("0.00000001")  # NUMERIC(24,8)

FIXTURE_LABEL = "FIXTURE — NOT LEGALLY VERIFIED (shared/11 FIX)"

#: FIX rates of shared/11 (fractions). Never legal values; the label travels with every snapshot.
FIX = {
    "iva.general": D("0.19"),
    "retefuente.ventas": D("0.025"),  # S1 renta 2.5 %
    "reteiva": D("0.15"),  # S1 ReteIVA 15 % of VAT
    "reteica.bogota": D("0.00966"),  # S1 ReteICA 9.66 per mil
    "retefuente.compras": D("0.025"),  # S2 renta compras 2.5 %
    "retefuente.servicios": D("0.04"),  # S9 services 4 %
    "retefuente.servicios.base_uvt": D("2"),  # S9 (Decreto 572 parameters, DISPUTED)
    "uvt.2026": D("52374"),
    "smmlv.2026": D("1750905"),  # DISPUTED flag on (shared/11 demo tenant)
    "auxilio_transporte.2026": D("249095"),
    "salud.empleado": D("0.04"),
    "pension.empleado": D("0.04"),
    "pension.empleador": D("0.12"),
    "ccf": D("0.04"),
    "arl.clase_i": D("0.00522"),
    "cesantias.interes_anual": D("0.12"),
    "depreciacion.fiscal.computo_anual": D("0.20"),  # S8 fiscal book 20 %/year
    "recargo.nocturno": D("0.35"),
}


def round_cop(x: Decimal) -> Decimal:
    """Document totals: ROUND_HALF_UP to the COP minor unit (D-001)."""
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def q4(x: Decimal) -> Decimal:
    """Amount precision, half-even (shared/07 §7 issue value)."""
    return x.quantize(AMOUNT, rounding=ROUND_HALF_EVEN)


def q8(x: Decimal) -> Decimal:
    """Unit-cost precision, half-even (shared/07 §7 WAC)."""
    return x.quantize(UNIT_COST, rounding=ROUND_HALF_EVEN)


# --------------------------------------------------------------------------------------- valuation


@dataclass
class WacItem:
    """Perpetual weighted average (shared/07 §7) with the residual-on-zero rule."""

    qty: Decimal = ZERO
    value: Decimal = ZERO
    avg: Decimal = ZERO

    def receive(self, qty: Decimal, unit_cost: Decimal) -> Decimal:
        value_in = q4(qty * unit_cost)
        self.qty += qty
        self.value += value_in
        self.avg = q8(self.value / self.qty)
        return value_in

    def issue(self, qty: Decimal) -> Decimal:
        if qty > self.qty:
            raise ValueError("insufficient_stock")
        out = self.value if qty == self.qty else q4(qty * self.avg)
        self.qty -= qty
        self.value -= out
        if self.qty == 0:
            self.value = ZERO
        return out

    def revalue(self, delta_value: Decimal) -> None:
        self.value += delta_value
        self.avg = q8(self.value / self.qty) if self.qty else ZERO


@dataclass
class Layer:
    key: str
    qty: Decimal
    unit_cost: Decimal
    remaining_qty: Decimal
    remaining_value: Decimal


@dataclass
class FifoItem:
    layers: list[Layer] = field(default_factory=list)

    def receive(self, key: str, qty: Decimal, unit_cost: Decimal) -> Decimal:
        value = q4(qty * unit_cost)
        self.layers.append(Layer(key, qty, unit_cost, qty, value))
        return value

    def issue(self, qty: Decimal) -> Decimal:
        if qty > self.qty:
            raise ValueError("insufficient_stock")
        need, out = qty, ZERO
        for layer in self.layers:
            if need == 0:
                break
            take = min(need, layer.remaining_qty)
            if take == 0:
                continue
            # The last unit of a layer takes the residual value (shared/07 §7).
            value = layer.remaining_value if take == layer.remaining_qty else q4(take * layer.unit_cost)
            layer.remaining_qty -= take
            layer.remaining_value -= value
            need -= take
            out += value
        return out

    def revalue_layer(self, key: str, delta_per_unit: Decimal) -> Decimal:
        """Price difference on a receipt: the remaining qty of that layer absorbs Δ; returns the
        on-hand part (the consumed part goes to COGS)."""
        layer = next(x for x in self.layers if x.key == key)
        onhand_part = q4(layer.remaining_qty * delta_per_unit)
        layer.unit_cost += delta_per_unit
        layer.remaining_value += onhand_part
        return onhand_part

    @property
    def qty(self) -> Decimal:
        return sum((x.remaining_qty for x in self.layers), ZERO)

    @property
    def value(self) -> Decimal:
        return sum((x.remaining_value for x in self.layers), ZERO)


def price_difference(received_qty: Decimal, on_hand_qty: Decimal, delta_per_unit: Decimal) -> tuple[Decimal, Decimal]:
    """Vendor invoice price ≠ receipt price (shared/07 §2): (to INVENTORY, to COGS)."""
    still = min(received_qty, on_hand_qty)
    total = q4(received_qty * delta_per_unit)
    to_inventory = q4(still * delta_per_unit)
    return to_inventory, total - to_inventory


def allocate(amount: Decimal, bases: Sequence[Decimal], precision: Decimal = AMOUNT) -> list[Decimal]:
    """Proportional allocation; the last line takes the residual (S3 rounding rule)."""
    total = sum(bases, ZERO)
    if total == 0:
        raise ValueError("allocation basis is zero")
    out = [(amount * b / total).quantize(precision, rounding=ROUND_DOWN) for b in bases[:-1]]
    out.append(amount - sum(out, ZERO))
    return out


# --------------------------------------------------------------------------------------- taxes


@dataclass(frozen=True)
class SaleTotals:
    net: Decimal
    vat: Decimal
    total: Decimal
    wht_renta: Decimal
    wht_iva: Decimal
    wht_ica: Decimal

    @property
    def receipt(self) -> Decimal:
        return self.total - self.wht_renta - self.wht_iva - self.wht_ica


def sale_totals(qty: Decimal, price: Decimal, *, withholding_agent: bool = True) -> SaleTotals:
    net = round_cop(qty * price)
    vat = round_cop(net * FIX["iva.general"])
    renta = round_cop(net * FIX["retefuente.ventas"]) if withholding_agent else ZERO
    reteiva = round_cop(vat * FIX["reteiva"]) if withholding_agent else ZERO
    reteica = round_cop(net * FIX["reteica.bogota"]) if withholding_agent else ZERO
    return SaleTotals(net, vat, net + vat, renta, reteiva, reteica)


def vendor_invoice(net: Decimal, *, wht_rate: Decimal = FIX["retefuente.compras"]) -> dict[str, Decimal]:
    vat = round_cop(net * FIX["iva.general"])
    wht = round_cop(net * wht_rate)
    return {"net": net, "vat": vat, "wht": wht, "ap": net + vat - wht}


# --------------------------------------------------------------------------------------- payroll (S6)


def payroll_s6(salary: Decimal = D("3000000"), days: int = 30, exonerated: bool = True) -> dict[str, Decimal]:
    smmlv = FIX["smmlv.2026"]
    transport = FIX["auxilio_transporte.2026"] if salary <= 2 * smmlv else ZERO
    earned = q4(salary * days / 30)
    health = round_cop(earned * FIX["salud.empleado"])
    pension = round_cop(earned * FIX["pension.empleado"])
    fsp = ZERO if earned < 4 * smmlv else round_cop(earned * D("0.01"))
    taxable = (earned - health - pension) * D("0.75")  # 25 % exempt labour income (FIX simplification)
    taxable_uvt = (taxable / FIX["uvt.2026"]).quantize(CENT, rounding=ROUND_HALF_UP)
    withholding = ZERO if taxable_uvt <= 95 else None  # first bracket 0-95 UVT (FIX)
    if withholding is None:
        raise ValueError("S6 fixture only covers the 0 % bracket")
    net = earned + transport - health - pension - fsp - withholding
    base_prov = earned + transport
    cesantias = round_cop(base_prov / 12)
    return {
        "earned": earned,
        "transport": transport,
        "health_employee": health,
        "pension_employee": pension,
        "fsp": fsp,
        "taxable": taxable,
        "taxable_uvt": taxable_uvt,
        "withholding": withholding,
        "net": net,
        "pension_employer": round_cop(earned * FIX["pension.empleador"]),
        "health_employer": ZERO if exonerated else round_cop(earned * D("0.085")),
        "sena": ZERO if exonerated else round_cop(earned * D("0.02")),
        "icbf": ZERO if exonerated else round_cop(earned * D("0.03")),
        "ccf": round_cop(earned * FIX["ccf"]),
        "arl": round_cop(earned * FIX["arl.clase_i"]),
        "cesantias": cesantias,
        "int_cesantias": round_cop(cesantias * FIX["cesantias.interes_anual"] / 12),
        "prima": cesantias,
        "vacaciones": round_cop(earned * 15 / 360),
    }


# --------------------------------------------------------------------------------------- time-keeping (S7)


@dataclass(frozen=True)
class DatedRule:
    valid_from: dt.date
    value: Decimal | int


#: FIX: dated labour parameters (R-LB-04/05, values to confirm by legal review — shared/11 S7).
NIGHT_START = (DatedRule(dt.date(2000, 1, 1), 21), DatedRule(dt.date(2025, 12, 25), 19))
SUNDAY_SURCHARGE = (
    DatedRule(dt.date(2000, 1, 1), D("0.75")),
    DatedRule(dt.date(2025, 7, 1), D("0.80")),
    DatedRule(dt.date(2026, 7, 1), D("0.90")),
    DatedRule(dt.date(2027, 7, 1), D("1.00")),
)
WEEKLY_MAX_HOURS = (
    DatedRule(dt.date(2025, 7, 15), 44),
    DatedRule(dt.date(2026, 7, 15), 42),
)


def dated(rules: Sequence[DatedRule], day: dt.date) -> Decimal | int:
    hit = [r for r in rules if r.valid_from <= day]
    if not hit:
        raise LookupError(f"no rule valid on {day}")
    return hit[-1].value


def night_hours(day: dt.date, start_h: int, end_h: int) -> int:
    """Hours of [start_h, end_h) on ``day`` inside the night window (start .. 06:00)."""
    begin = int(dated(NIGHT_START, day))
    return sum(1 for h in range(start_h, end_h) if h >= begin or h < 6)


def hourly_value(salary: Decimal, divisor: int) -> Decimal:
    return salary / divisor


def surcharge_amount(salary: Decimal, divisor: int, hours: int, rate: Decimal) -> Decimal:
    return round_cop(hourly_value(salary, divisor) * hours * rate)


def is_habitual_sunday(sundays_worked_in_month: int) -> bool:
    """Third Sunday worked in the calendar month → habitual (compensatory rest rules, T7-7)."""
    return sundays_worked_in_month >= 3


# --------------------------------------------------------------------------------------- fixed assets (S8)


def straight_line(cost: Decimal, months: int, salvage: Decimal = ZERO) -> list[Decimal]:
    base = cost - salvage
    monthly = round_cop(base / months)
    sched = [monthly] * (months - 1)
    sched.append(base - monthly * (months - 1))  # last month takes the residual
    return sched


def fiscal_monthly(cost: Decimal, annual_rate: Decimal = FIX["depreciacion.fiscal.computo_anual"]) -> Decimal:
    return round_cop(cost * annual_rate / 12)


def month_add(day: dt.date, n: int) -> dt.date:
    m = day.month - 1 + n
    return dt.date(day.year + m // 12, m % 12 + 1, 1)


def month_end(first: dt.date) -> dt.date:
    return month_add(first, 1) - dt.timedelta(days=1)


# --------------------------------------------------------------------------------------- DS weekly (S9)


def last_business_day_of_week(any_day: dt.date, is_business_day: Callable[[dt.date], bool]) -> dt.date:
    """Friday of ``any_day``'s week, moved back to the previous business day when it is a holiday."""
    friday = any_day + dt.timedelta(days=4 - any_day.weekday())
    day = friday
    while not is_business_day(day):
        day -= dt.timedelta(days=1)
    return day


def services_withholding(payment: Decimal, *, base_uvt: Decimal, uvt: Decimal, rate: Decimal) -> Decimal:
    return round_cop(payment * rate) if payment >= base_uvt * uvt else ZERO


# --------------------------------------------------------------------------------------- FX (S12)


def fx(amount: Decimal, rate: Decimal) -> Decimal:
    return q4(amount * rate)


# --------------------------------------------------------------------------------------- NIT check digit (S11)

_NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


def nit_check_digit(number: str) -> int:
    """DIAN modulo-11 check digit, written independently of ``localization_co.nit``."""
    digits = [int(c) for c in reversed(number)]
    total = sum(d * w for d, w in zip(digits, _NIT_WEIGHTS, strict=False))
    r = total % 11
    return r if r in (0, 1) else 11 - r
