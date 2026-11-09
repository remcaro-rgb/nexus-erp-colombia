"""G0 gate test T-X-QA-07 (gate-owned, written by the wave-01 integrator).

CCR-FEAT-001 is recorded in the G0 attempt, and every ``kernel.interfaces`` stub
named in FEATURES-ADDENDUM exists as a typed Protocol with a test double.
"""
from __future__ import annotations

import hashlib
import inspect
import pathlib
import re
import typing

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDENDUM = ROOT / "docs/spec/FEATURES-ADDENDUM.md"
REGISTRY = ROOT / "docs/spec/features/feature-registry.json"
RECORD = ROOT / "docs/gates/G0-CCR-FEAT-001.md"


def _named_stubs() -> set[str]:
    text = ADDENDUM.read_text(encoding="utf-8")
    return set(re.findall(r"kernel\.interfaces\.([A-Z][A-Za-z0-9_]*)", text))


def _public_methods(proto):
    return {n: m for n, m in inspect.getmembers(proto, inspect.isfunction) if not n.startswith("_")}


def test_T_X_QA_07_ccr_feat_001_recorded_in_g0_attempt():
    assert "BINDING" in ADDENDUM.read_text(encoding="utf-8")
    rec = RECORD.read_text(encoding="utf-8")
    assert "CCR-FEAT-001" in rec
    assert "G0 attempt" in rec
    assert hashlib.sha256(REGISTRY.read_bytes()).hexdigest() in rec, "record must pin the adopted registry"


def test_T_X_QA_07_every_addendum_stub_is_a_typed_protocol_with_a_double():
    from kernel import interfaces
    from kernel.testing import fakes

    names = _named_stubs()
    # the addendum names at least these nine stubs; the list is parsed, not hard-coded
    assert {"Dimensions", "Inventory", "DocumentRenderer", "FiscalDocuments", "PaymentStatus",
            "PayrollPayables", "Notifications", "Labels", "ArOpenItems"} <= names, names
    doubles = [obj for _, obj in inspect.getmembers(fakes, inspect.isclass) if obj.__module__ == fakes.__name__]
    for name in sorted(names):
        proto = getattr(interfaces, name, None)
        assert proto is not None, f"kernel.interfaces.{name} missing"
        assert getattr(proto, "_is_protocol", False), f"{name} is not a typing.Protocol"
        meths = _public_methods(proto)
        assert meths, f"{name} declares no methods"
        for mname, meth in meths.items():
            hints = typing.get_type_hints(meth)
            assert "return" in hints, f"{name}.{mname} has no return annotation"
            params = [p for p in inspect.signature(meth).parameters if p != "self"]
            missing = [p for p in params if p not in hints]
            assert not missing, f"{name}.{mname} untyped parameters {missing}"
        impls = []
        for cls in doubles:
            if not all(callable(getattr(cls, m, None)) for m in meths):
                continue
            if all(list(inspect.signature(getattr(cls, m)).parameters)[1:] ==
                   [p for p in inspect.signature(meths[m]).parameters if p != "self"] for m in meths):
                impls.append(cls)
        assert impls, f"no test double in kernel.testing.fakes implements {name}"
        if getattr(proto, "_is_runtime_protocol", False):
            assert any(isinstance(c(), proto) for c in impls), name
