"""CUFE/CUDE/CUNE/CUDS (T-P15-02, T-P15-03)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from einvoice import hashing
from kernel.errors import ValidationFailed

SPEC = Path(__file__).resolve().parents[3] / "specs" / "fiscal-hash"


def _vectors(name: str) -> list[dict[str, object]]:
    return json.loads((SPEC / "vectors" / name).read_text(encoding="utf-8"))["vectors"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("vector", _vectors("annex-fe-1.8.json"), ids=lambda v: str(v["id"]))
def test_T_P15_02_unique_codes_match_the_annex_worked_examples(vector: dict[str, object]) -> None:
    inputs = vector["inputs"]
    assert isinstance(inputs, dict)
    assert hashing.composition(str(vector["kind"]), inputs) == vector["composition"]
    assert hashing.compute(str(vector["kind"]), inputs) == vector["expected"]
    if "annex_published_hash" in vector:  # documented annex erratum: published hash omits the Software-PIN
        without_pin = str(vector["composition"]).removesuffix("10201" + str(inputs["TipoAmbiente"]))
        assert (
            hashlib.sha384((without_pin + str(inputs["TipoAmbiente"])).encode()).hexdigest()
            == vector["annex_published_hash"]
        )


@pytest.mark.parametrize("vector", _vectors("synthetic.json"), ids=lambda v: str(v["id"]))
def test_T_P15_03_python_implementation_matches_all_vectors(vector: dict[str, object]) -> None:
    inputs = vector["inputs"]
    assert isinstance(inputs, dict)
    assert hashing.composition(str(vector["kind"]), inputs) == vector["composition"]
    assert hashing.compute(str(vector["kind"]), inputs) == vector["expected"]


def test_T_P15_03_language_neutral_spec_equals_the_implementation() -> None:
    assert json.loads((SPEC / "formulas.json").read_text(encoding="utf-8")) == hashing.formulas_json()


def test_T_P15_03_amounts_are_truncated_not_rounded_and_inputs_required() -> None:
    assert hashing.format_amount(Decimal("100.999")) == "100.99"
    assert hashing.format_amount("0") == "0.00"
    with pytest.raises(ValidationFailed):
        hashing.format_amount("NaN")
    with pytest.raises(ValidationFailed):
        hashing.compute("CUFE", {"NumFac": "X"})


@given(st.decimals(min_value=Decimal(0), max_value=Decimal("1e12"), places=6, allow_nan=False))
def test_T_P15_03_amount_format_property(value: Decimal) -> None:
    out = hashing.format_amount(value)
    _whole, _, frac = out.partition(".")
    assert len(frac) == 2 and "," not in out
    assert Decimal(out) <= value < Decimal(out) + Decimal("0.01")
