"""Colombian localization: NIT DV (T-P06-01), holidays (T-P06-08), words, formats, DIVIPOLA."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from localization_co import holidays as hol
from localization_co import nit
from localization_co.amount_words import amount_in_words, integer_to_words
from localization_co.formats import format_date, format_money, format_number, parse_number

D = Decimal

# ----------------------------------------------------------------------------- T-P06-01

# Publicly known NITs of public entities and listed companies (number, DV).
KNOWN_NITS = [
    ("800197268", 4),  # DIAN
    ("899999068", 1),  # Ecopetrol S.A.
    ("890903938", 8),  # Bancolombia S.A.
    ("860034313", 7),  # Banco Davivienda S.A.
    ("860002964", 4),  # Banco de Bogotá S.A.
]


@pytest.mark.parametrize(("number", "dv"), KNOWN_NITS)
def test_T_P06_01_dv_of_known_nits(number: str, dv: int) -> None:
    assert nit.check_digit(number) == dv
    assert nit.validate(number, dv).valid
    assert nit.validate(f"{int(number):,}".replace(",", ".") + f"-{dv}").valid
    assert not nit.validate(number, (dv + 1) % 10).valid


def test_T_P06_01_dv_rules_and_errors() -> None:
    assert nit.WEIGHTS == (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
    result = nit.validate("800197268")
    assert result.valid and result.check_digit == 4 and result.provided_check_digit is None
    assert result.formatted == "800.197.268-4"
    for bad in ("", "80019726A", "1" * 16):
        with pytest.raises(nit.NitInvalid) as exc:
            nit.check_digit(bad)
        assert exc.value.code == "nit_invalid"
    with pytest.raises(nit.NitInvalid):
        nit.validate("800197268", "44")
    with pytest.raises(nit.NitInvalid) as exc:
        nit.require_valid("800197268", 5)
    assert "esperado 4" in str(exc.value)
    assert {d.code for d in nit.DOCUMENT_TYPES} == {"CC", "CE", "NIT", "PP", "TI", "PEP", "PPT", "NIT_EXT"}


@given(st.integers(min_value=1, max_value=10**15 - 1))
def test_T_P06_01_dv_properties(number: int) -> None:
    digits = str(number)
    dv = nit.check_digit(digits)
    assert 0 <= dv <= 9
    assert nit.validate(digits, dv).valid
    assert sum(nit.validate(digits, other).valid for other in range(10)) == 1
    assert nit.check_digit("000" + digits) == dv  # leading zeros do not change the DV


# ----------------------------------------------------------------------------- T-P06-08

HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-12", "2026-03-23", "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-18", "2026-06-08",
    "2026-06-15", "2026-06-29", "2026-07-20", "2026-08-07", "2026-08-17", "2026-10-12", "2026-11-02", "2026-11-16",
    "2026-12-08", "2026-12-25",
}  # fmt: skip
HOLIDAYS_2027 = {
    "2027-01-01", "2027-01-11", "2027-03-22", "2027-03-25", "2027-03-26", "2027-05-01", "2027-05-10", "2027-05-31",
    "2027-06-07", "2027-07-05", "2027-07-20", "2027-08-07", "2027-08-16", "2027-10-18", "2027-11-01", "2027-11-15",
    "2027-12-08", "2027-12-25",
}  # fmt: skip


def test_T_P06_08_holidays_2026() -> None:
    assert hol.easter_sunday(2026) == dt.date(2026, 4, 5)
    got = hol.holidays_for_year(2026)
    assert {h.date.isoformat() for h in got} == HOLIDAYS_2026
    by_name = {h.name: h for h in got}
    assert by_name["Día de los Reyes Magos"].date == dt.date(2026, 1, 12)  # Tue 6 Jan → Mon 12 Jan
    assert by_name["Ascensión del Señor"].rule == "EASTER_MOVED"
    assert by_name["Viernes Santo"].rule == "EASTER" and by_name["Navidad"].rule == "FIXED"


def test_T_P06_08_holidays_2027() -> None:
    assert hol.easter_sunday(2027) == dt.date(2027, 3, 28)
    assert {h.date.isoformat() for h in hol.holidays_for_year(2027)} == HOLIDAYS_2027
    assert dt.date(2027, 5, 1).weekday() == 5  # fixed holidays stay on Saturday


@pytest.mark.parametrize("year", [2026, 2027])
def test_T_P06_08_moved_holidays_fall_on_monday(year: int) -> None:
    for h in hol.holidays_for_year(year):
        if h.rule in ("MOVED_TO_MONDAY", "EASTER_MOVED"):
            assert h.date.weekday() == 0, h


def test_T_P06_08_admin_override_and_business_days() -> None:
    extra = hol.Override(dt.date(2026, 3, 9), True, "Día cívico (ajuste)")
    removed = hol.Override(dt.date(2026, 3, 23), False)
    got = {h.date for h in hol.holidays_for_year(2026, [extra, removed])}
    assert dt.date(2026, 3, 9) in got and dt.date(2026, 3, 23) not in got
    assert hol.is_holiday(dt.date(2026, 12, 25)) and not hol.is_holiday(dt.date(2026, 12, 24))
    # Friday 2026-04-03 is Viernes Santo: 5 business days from Wed 2026-04-01 → Thu 2026-04-09
    assert hol.add_business_days(dt.date(2026, 4, 1), 5) == dt.date(2026, 4, 10)
    assert not hol.is_business_day(dt.date(2026, 4, 2)) and hol.is_business_day(dt.date(2026, 4, 6))
    with pytest.raises(ValueError):
        hol.easter_sunday(1500)


@pytest.mark.django_db
def test_T_P06_08_override_stored_per_company(p06_ctx: Any, company: Any) -> None:
    from kernel.testing.tenancy import bound
    from localization_co.models import HolidayOverride

    with bound(p06_ctx):  # localization_co_holiday_override has forced RLS
        HolidayOverride.objects.create(tenant_id=p06_ctx.tenant_id, company_id=company, date=dt.date(2027, 3, 1),
                                       add=True, name="Ajuste", reason="decreto local (sintético)")  # fmt: skip
        rows = [hol.Override(o.date, o.add, o.name) for o in HolidayOverride.objects.filter(company_id=company)]
    assert dt.date(2027, 3, 1) in {h.date for h in hol.holidays_for_year(2027, rows)}


# ----------------------------------------------------------------------------- words / formats


@pytest.mark.parametrize(
    ("value", "words"),
    [("0", "CERO PESOS M/CTE"), ("1", "UN PESO M/CTE"), ("21", "VEINTIÚN PESOS M/CTE"), ("100", "CIEN PESOS M/CTE"),
     ("101", "CIENTO UN PESOS M/CTE"), ("1000000", "UN MILLÓN DE PESOS M/CTE"),
     ("1190000.50", "UN MILLÓN CIENTO NOVENTA MIL PESOS CON 50/100 M/CTE"),
     ("52374", "CINCUENTA Y DOS MIL TRESCIENTOS SETENTA Y CUATRO PESOS M/CTE"),
     ("21000000", "VEINTIÚN MILLONES DE PESOS M/CTE"), ("1000000000", "MIL MILLONES DE PESOS M/CTE"),
     ("2000000000000", "DOS BILLONES DE PESOS M/CTE")],
)  # fmt: skip
def test_P06_amount_in_words(value: str, words: str) -> None:
    assert amount_in_words(D(value)) == words


def test_P06_amount_in_words_rejects_float_and_negative() -> None:
    with pytest.raises(Exception) as exc:
        amount_in_words(1.5)  # type: ignore[arg-type]
    assert exc.value.code == "validation_failed"
    with pytest.raises(Exception):
        amount_in_words(D("-1"))
    assert amount_in_words(D("10"), "USD") == "DIEZ DÓLARES"


@given(st.integers(min_value=0, max_value=10**15))
def test_P06_integer_to_words_total(n: int) -> None:
    words = integer_to_words(n)
    assert words and "  " not in words and not words.endswith(" ")


def test_P06_formats() -> None:
    assert format_number(D("1234567.895")) == "1.234.567,90"
    assert format_money(D("-1500")) == "-$ 1.500,00"
    assert format_money(D("10"), "USD") == "USD 10,00"
    assert format_date(dt.date(2026, 11, 9)) == "09/11/2026"
    assert parse_number("$ 1.234.567,89") == D("1234567.89")
    with pytest.raises(Exception):
        parse_number("abc")


@pytest.mark.django_db
def test_P06_divipola_and_code_lists_load() -> None:
    from localization_co.loaders import load_defaults
    from localization_co.models import CodeListVersion, Municipality

    counts = load_defaults()
    assert counts == {"departments": 10, "municipalities": 10, "code_lists": 2}
    assert load_defaults() == counts  # idempotent
    assert Municipality.objects.get(code="05001").department.name == "Antioquia"
    lists = CodeListVersion.objects.filter(list_code="dian.document_type")
    assert lists.count() == 1 and lists[0].synthetic and not lists[0].verified
