"""P22a contract runner: implemented routes vs the frozen OpenAPI (UR-22, C1-7: nothing outside the
public API; the UI and every agent use only documented operations)."""

from __future__ import annotations

import pytest
from qa_harness import defects, openapi
from qa_harness.routes import implemented_routes, match_contract

# DEF-P22-001: kernel routes P01 shipped before P02's contract documented them.
KNOWN_UNDOCUMENTED = frozenset(
    {"/api/health/live", "/api/health/ready", "/api/v1/system/environment", "/api/v1/ui/context", "/api/v1/schema"}
)


def _undocumented() -> set[str]:
    return {t for t, ops in match_contract(implemented_routes()).items() if not ops}


@pytest.mark.req("UR-22")
def test_p22a_no_new_undocumented_api_route():
    """Regression guard that runs green today: no route beyond the filed defect is undocumented."""
    extra = _undocumented() - KNOWN_UNDOCUMENTED
    assert not extra, f"undocumented API routes (add them to contracts/src or remove them): {sorted(extra)}"


@defects.xfail("DEF-P22-001")
@pytest.mark.req("UR-22")
def test_p22a_every_implemented_api_route_is_in_the_contract():
    assert not _undocumented()


@pytest.mark.req("UR-22")
def test_p22a_documented_routes_resolve_to_contract_operations():
    matched = {t: ops for t, ops in match_contract(implemented_routes()).items() if ops}
    for template, ops in matched.items():
        assert all(op.permission for op in ops), template
        assert {op.path for op in ops} <= {o.path for o in openapi.operations()}


def test_p22a_route_matcher_ignores_parameter_names():
    from qa_harness.routes import Route

    fake = [Route("/api/v1/sales/parties/{pk}", "x", "v"), Route("/api/v1/nope", "y", "v")]
    m = match_contract(fake)
    assert {op.method for op in m["/api/v1/sales/parties/{pk}"]} == {"get", "patch", "delete"}
    assert m["/api/v1/nope"] == []
