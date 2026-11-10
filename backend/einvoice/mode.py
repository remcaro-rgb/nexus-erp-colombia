"""Fiscal mode guard (C1-11, ADR-007, EG-1..EG-3).

``ERP_FISCAL_MODE`` (setting or environment; default ``sandbox``). This DEV-only edition never transmits in
``production``: the simulator refuses to start, and every adapter refuses environment ``1`` (producción)
unless the gate records exist — and even then :func:`require_transmission_allowed` refuses, because the
production transmission path is not part of this edition (it stays disabled until EG-1/EG-2/EG-3).
"""

from __future__ import annotations

import os

from django.conf import settings

from einvoice.errors import ProductionRefused

PRODUCTION = "production"
SANDBOX = "sandbox"


def fiscal_mode() -> str:
    value = getattr(settings, "ERP_FISCAL_MODE", None) or os.environ.get("ERP_FISCAL_MODE") or SANDBOX
    return str(value).strip().lower()


def dian_integration_mode() -> str:
    modes = getattr(settings, "ERP_INTEGRATIONS", {}) or {}
    return str(modes.get("dian", "FIXTURE_NOT_CERTIFIED"))


def is_production() -> bool:
    return fiscal_mode() == PRODUCTION


def refuse_simulator_in_production() -> None:
    if is_production():
        raise ProductionRefused("the SandboxSimulator cannot run when FISCAL_MODE=production")


def require_transmission_allowed(environment: str, gate_records: frozenset[str] = frozenset()) -> None:
    """Environment ``1`` (producción) or FISCAL_MODE=production → refused without EG-1/EG-3 records, and
    refused in any case by this DEV-only edition."""
    if dian_integration_mode() == "DISABLED":
        raise ProductionRefused("DIAN integration is DISABLED (ERP_INTEGRATION_DIAN)")
    if environment != "1" and not is_production():
        return
    missing = sorted({"EG-1", "EG-3"} - gate_records)
    if missing:
        raise ProductionRefused(f"production transmission refused: missing external gate records {', '.join(missing)}")
    raise ProductionRefused("production transmission is not available in the DEV-only edition")
