"""Defects filed by P22 (prompt P22 "Mission"): failing tests stay in the P22 suites marked
``xfail(strict=True, reason="DEF-<id>")`` until the owner fixes the module; a strict xfail that starts
passing fails the run, so the marker must then be removed. G-gates fail while any strict xfail remains
(``open_defects()`` lists them for the gate evidence).

Each defect has a change-request file ``contracts/change-requests/P22a-<n>.md`` / ``P22b-<n>.md`` for
its owner. ``PENDING`` lists modules a scenario leg needs that are not merged yet (P22b).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Defect:
    id: str
    owner: str
    summary: str
    change_request: str


DEFECTS: dict[str, Defect] = {
    d.id: d
    for d in (
        Defect(
            "DEF-P22-001",
            "P02",
            "implemented kernel routes /api/health/live, /api/health/ready, /api/v1/system/environment, "
            "/api/v1/ui/context, /api/v1/schema are missing from the frozen OpenAPI (D-P02, UR-22)",
            "contracts/change-requests/P22a-2.md",
        ),
        Defect(
            "DEF-P22-002",
            "P04",
            "anonymous API requests answer 403 without WWW-Authenticate; the contract declares 401 "
            "Unauthorized (problem+json) for every protected operation",
            "contracts/change-requests/P22a-3.md",
        ),
        Defect(
            "DEF-P22-003",
            "P01",
            "GET /api/v1/schema answers 200 to anonymous callers (drf-spectacular SERVE_PERMISSIONS defaults "
            "to AllowAny); only health/live, health/ready and system/environment may be anonymous (D-ALL 1, D-P01)",
            "contracts/change-requests/P22a-4.md",
        ),
        Defect(
            "DEF-P22-004",
            "P02",
            "wave-2 routes /api/portal/v1/invitations:accept (P04), /api/v1/kpis/aprobaciones (P07) and "
            "/api/v1/tax/parameters/{id}/impact (P06) are implemented but missing from the frozen OpenAPI (UR-22)",
            "contracts/change-requests/P22b-1.md",
        ),
        Defect(
            "DEF-P22-005",
            "P02",
            "role template 'vendedor' reads inventory items with standard_cost (confidential) and valuation_method: "
            "S1 step 7 requires that vendedor1@A sees no cost fields (API, export, report, AI, MCP)",
            "contracts/change-requests/P22b-2.md",
        ),
        Defect(
            "DEF-P22-006",
            "P02",
            "role template 'vendedor' has no sales.invoice.read (own customers): S1 step 7 requires that "
            "vendedor1@A sees the invoice of an own customer",
            "contracts/change-requests/P22b-2.md",
        ),
        Defect(
            "DEF-P22-007",
            "P05",
            "PostingRequest cannot address one book: every entry is mirrored to NIIF and FISCAL, so the "
            "book-specific depreciation entries of shared/07 §5 (S8: NIIF 133,333.33 vs fiscal 80,000 per month) "
            "cannot be posted",
            "contracts/change-requests/P22b-3.md",
        ),
        Defect(
            "DEF-P22-008",
            "P01",
            "kernel.parameters.enforce raises ParameterUnknown(..., code=<parameter code>): the keyword overwrites the "
            "error code, so the problem+json type becomes '.../smmlv.2027' instead of '.../parameter_unknown' and "
            "callers cannot recognise the fail-closed error (C1-9)",
            "contracts/change-requests/P22b-4.md",
        ),
        Defect(
            "DEF-P22-009",
            "P04",
            "iam.api.base.SecuredView/ModuleSecuredView open the request's tenant scope (tenancy.activate + "
            "transaction.atomic + SET LOCAL binding) in initial() and close it only in finalize_response(), which DRF "
            "skips when an unhandled exception escapes the handler: the transaction block and the activated tenant "
            "context leak onto the connection/thread, and when the garbage collector later finalises the suspended "
            "scope its Atomic.__exit__ runs on the collecting thread's connection (ADR-003, invariant 12)",
            "contracts/change-requests/P22b-5.md",
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Pending:
    """A scenario leg whose owning module is not merged yet. The leg calls :func:`require`: while the
    module is absent the test reports XFAIL (never PASSED, never skipped); once the module is installed
    the same test runs against it for real and passes or fails on its own merits."""

    id: str
    owner: str
    app_label: str
    summary: str


PENDING: dict[str, Pending] = {
    p.id: p
    for p in (
        Pending("PEND-P09", "P09a", "inventory", "StockService/Inventory (valuation layers, quants, row locks)"),
        Pending("PEND-P10", "P10a", "sales", "sales invoice / receipt / credit note documents and their API"),
        Pending("PEND-P11", "P11a", "purchasing", "goods receipt, vendor invoice, landed cost, documento soporte"),
        Pending("PEND-P12", "P12", "pos", "POS terminal, offline draft, session close"),
        Pending("PEND-P13", "P13b", "payroll", "payroll run, nómina electrónica, provisions"),
        Pending("PEND-P13T", "P13a", "timekeeping", "time-keeping surcharges and weekly limits"),
        Pending("PEND-P14", "P14", "assets", "fixed asset register, two-book depreciation, disposal"),
        Pending("PEND-P15", "P15", "einvoice", "FE/DS/NE generation, CUFE/CUDE/CUNE, sandbox status, contingency"),
        Pending("PEND-P16", "P16a", "treasury", "payments, TRM adapter, FX revaluation, intercompany mirror"),
        Pending("PEND-P16C", "P16b", "consolidation", "consolidation eliminations"),
        Pending("PEND-P21", "P21", "onboarding", "onboarding agent, dry-run, batch confirmation"),
    )
}


def installed(pending_id: str) -> bool:
    from django.apps import apps

    return apps.is_installed(PENDING[pending_id].app_label)


def require(*pending_ids: str) -> None:
    """Imperative XFAIL while any named module is absent (see :class:`Pending`)."""
    import pytest

    missing = [PENDING[p] for p in pending_ids if not installed(p)]
    if missing:
        pytest.xfail(
            "; ".join(f"{p.id} (owner {p.owner}): '{p.app_label}' not installed - {p.summary}" for p in missing)
        )


def reason(defect_id: str) -> str:
    d = DEFECTS[defect_id]
    return f"{d.id} (owner {d.owner}): {d.summary}"


def xfail(defect_id: str):  # -> pytest.MarkDecorator
    import pytest

    return pytest.mark.xfail(strict=True, reason=reason(defect_id))


def open_defects() -> list[Defect]:
    return sorted(DEFECTS.values(), key=lambda d: d.id)
