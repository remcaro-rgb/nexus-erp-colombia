"""T-P12-13 / T-P12-06 — the [A] flag ``pos_offline_fiscal_selling`` (ADR-015, EG-13).

* Production startup refuses the flag ``on`` without an APPROVED EG-13 evidence file; DEV refuses it outright
  (the capability is not built in this edition).
* No server or client path assigns fiscal numbers outside the checkout transaction: the only allocations of the DEE
  POS sequence happen in ``pos.services.common.fiscal_number`` (called from ``checkout`` only) and the frontend
  never computes numbers or a CUDE (static check over ``frontend/src/features/pos``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from pos.registry import check_offline_flag

REPO = Path(__file__).resolve().parents[3]


def test_T_P12_13_production_refuses_offline_flag_without_eg13_evidence(tmp_path: Path) -> None:
    check_offline_flag("production", None, None)  # flag off: nothing to check
    check_offline_flag("production", "off", None)
    with pytest.raises(ImproperlyConfigured, match="EG-13"):
        check_offline_flag("production", "on", None)
    missing = tmp_path / "missing.json"
    with pytest.raises(ImproperlyConfigured, match="unreadable"):
        check_offline_flag("production", "true", str(missing))
    pending = tmp_path / "eg13.json"
    pending.write_text(json.dumps([{"gate": "EG-13", "status": "PENDING"}]), encoding="utf-8")
    with pytest.raises(ImproperlyConfigured, match="APPROVED"):
        check_offline_flag("production", "1", str(pending))
    approved = tmp_path / "eg13-ok.json"
    approved.write_text(json.dumps({"gate": "EG-13", "status": "APPROVED"}), encoding="utf-8")
    check_offline_flag("production", "on", str(approved))
    with pytest.raises(ImproperlyConfigured, match="not built"):
        check_offline_flag("dev", "on", str(approved))


def test_T_P12_06_no_fiscal_numbering_or_cude_outside_the_checkout_transaction() -> None:
    backend = REPO / "backend" / "pos"
    callers = []
    for path in backend.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bfiscal_number\(", text):
            callers.append(path.relative_to(backend).as_posix())
        assert "allocate_terminal_block" not in text, path
    assert sorted(callers) == ["services/checkout.py", "services/common.py"]
    checkout = (backend / "services" / "checkout.py").read_text(encoding="utf-8")
    assert checkout.count("fiscal_number(") == 1
    frontend = REPO / "frontend" / "src" / "features" / "pos"
    forbidden = re.compile(r"(?i)\b(cude|sha-?384|nextNumber|allocateNumber|fiscalNumber|numberBlock)\b")
    files = [p for p in frontend.rglob("*.ts*") if "__tests__" not in p.parts]
    assert files, "frontend POS feature missing"
    for path in files:
        code = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("//")
        )
        assert not forbidden.search(code), f"{path}: client-side fiscal numbering/CUDE is forbidden (ADR-015)"
