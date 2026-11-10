"""Legal parameters as formula inputs (F-PAY-FORMULA, D-025, C1-9).

A payroll formula reads a legal value by declaring an input whose name starts with ``param_``.
The name must be in :data:`PARAMETER_INPUTS` (the versioned catalogue below, which maps formula
identifiers to the dotted codes of the P06 parameter store). Values are never written here: they
are read from ``kernel.parameters.ParameterProvider`` for the calculation date of the run.

* unknown ``param_*`` name, missing value, or an UNKNOWN status → :class:`PayrollParameterUnknown`
  (``payroll_parameter_unknown``, fail closed);
* production rules without a legal-review stamp → the provider's ``parameter_not_verified``;
* every value read is snapshotted (``ParameterValue.snapshot()``) into the run, the payslip and
  each line, and labelled ``NOT LEGALLY VERIFIED`` unless it carries a legal-review stamp and is
  not a synthetic fixture.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from kernel import services
from kernel.errors import ParameterUnknown
from kernel.parameters import ParameterProvider, ParameterValue
from payroll.errors import PayrollParameterUnknown

PARAM_PREFIX = "param_"
NOT_LEGALLY_VERIFIED = "NOT LEGALLY VERIFIED"
CATALOGUE_VERSION = "2026.1"

#: Formula identifier → parameter code (P06 store).
PARAMETER_INPUTS: Mapping[str, str] = {
    "param_smmlv": "smmlv",
    "param_aux_transporte": "aux_transporte",
    "param_uvt": "uvt",
    "param_salud_empleado": "ss.salud.empleado",
    "param_salud_empleador": "ss.salud.empleador",
    "param_pension_empleado": "ss.pension.empleado",
    "param_pension_empleador": "ss.pension.empleador",
    "param_fsp_tarifa": "ss.fsp.tarifa",
    "param_fsp_umbral_smmlv": "ss.fsp.umbral_smmlv",
    "param_ibc_tope_smmlv": "ss.ibc.tope_smmlv",
    "param_ibc_no_salarial_pct": "ss.ibc.no_salarial_pct",
    "param_ccf": "parafiscal.ccf",
    "param_sena": "parafiscal.sena",
    "param_icbf": "parafiscal.icbf",
    "param_exoneracion_umbral_smmlv": "exoneracion.umbral_smmlv",
    "param_ret_lab_tope_pct": "ret_lab.depuracion.tope_pct",
    "param_ret_lab_tope_uvt": "ret_lab.depuracion.tope_uvt",
}


#: Inputs whose store type is ``percent``: the provider returns the fraction (0.04); formulas receive
#: percent units (4), the unit the store is written in and the DSL's ``percent(x, p)`` expects.
PERCENT_INPUTS: frozenset[str] = frozenset(
    {
        "param_salud_empleado",
        "param_salud_empleador",
        "param_pension_empleado",
        "param_pension_empleador",
        "param_fsp_tarifa",
        "param_ibc_no_salarial_pct",
        "param_ccf",
        "param_sena",
        "param_icbf",
        "param_ret_lab_tope_pct",
    }
)


def is_parameter_input(name: str) -> bool:
    return name.startswith(PARAM_PREFIX)


def parameter_code(name: str) -> str:
    """The store code of a ``param_*`` input; unknown names fail closed."""
    try:
        return PARAMETER_INPUTS[name]
    except KeyError:
        raise PayrollParameterUnknown(
            f"formula input {name!r} is not a known legal parameter (catalogue {CATALOGUE_VERSION})",
            input=name,
        ) from None


def legally_verified(value: ParameterValue) -> bool:
    return bool(value.legal_review_stamp) and not value.synthetic


def labelled_snapshot(value: ParameterValue, input_name: str) -> dict[str, Any]:
    snap = value.snapshot()
    verified = legally_verified(value)
    snap.update(
        {
            "input": input_name,
            "legally_verified": verified,
            "label": "" if verified else NOT_LEGALLY_VERIFIED,
        }
    )
    return snap


def _as_decimal(value: ParameterValue, name: str) -> Decimal:
    raw = value.value
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return Decimal(raw)
    if isinstance(raw, str):
        try:
            return Decimal(raw)
        except ArithmeticError:
            pass
    if isinstance(raw, Mapping) and "value" in raw:
        return Decimal(str(raw["value"]))
    raise PayrollParameterUnknown(
        f"parameter {value.code} is not a scalar decimal (formula input {name})", parameter=value.code, input=name
    )


@dataclass
class ParameterSet:
    """Legal parameters resolved for one company and calculation date (memoised per run)."""

    company_id: uuid.UUID
    on: dt.date
    provider: ParameterProvider | None = None
    _values: dict[str, ParameterValue] = field(default_factory=dict)

    def _provider(self) -> ParameterProvider:
        if self.provider is None:
            self.provider = services.get(ParameterProvider)
        return self.provider

    def get(self, name: str) -> ParameterValue:
        code = parameter_code(name)
        if code not in self._values:
            try:
                self._values[code] = self._provider().get(code, self.on, self.company_id)
            except ParameterUnknown as exc:
                raise PayrollParameterUnknown(
                    f"legal parameter {code} is UNKNOWN on {self.on.isoformat()} (fail closed)",
                    parameter=code,
                    input=name,
                    date=self.on.isoformat(),
                ) from exc
        return self._values[code]

    def inputs(self, names: Iterable[str]) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
        """``({input: Decimal}, [labelled snapshots])`` for the ``param_*`` names among ``names``."""
        values: dict[str, Decimal] = {}
        snaps: list[dict[str, Any]] = []
        for name in sorted(n for n in names if is_parameter_input(n)):
            pv = self.get(name)
            value = _as_decimal(pv, name)
            values[name] = value * 100 if name in PERCENT_INPUTS else value
            snaps.append(labelled_snapshot(pv, name))
        return values, snaps

    def snapshot(self) -> dict[str, Any]:
        reverse = {code: name for name, code in PARAMETER_INPUTS.items()}
        return {code: labelled_snapshot(v, reverse.get(code, code)) for code, v in sorted(self._values.items())}

    @property
    def all_verified(self) -> bool:
        return all(legally_verified(v) for v in self._values.values())
