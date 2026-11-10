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
# DEF-P22-004 (P22b): wave-2 module routes (P04, P07, P06) missing from the contract.
KNOWN_UNDOCUMENTED_W02 = frozenset(
    {"/api/portal/v1/invitations:accept", "/api/v1/kpis/aprobaciones", "/api/v1/tax/parameters/{pk}/impact"}
)


def _undocumented() -> set[str]:
    return {t for t, ops in match_contract(implemented_routes()).items() if not ops}


@pytest.mark.req("UR-22")
def test_p22a_no_new_undocumented_api_route():
    """Regression guard that runs green today: no route beyond the filed defect is undocumented."""
    extra = _undocumented() - KNOWN_UNDOCUMENTED - KNOWN_UNDOCUMENTED_W02
    assert not extra, f"undocumented API routes (add them to contracts/src or remove them): {sorted(extra)}"


@defects.xfail("DEF-P22-001")
@pytest.mark.req("UR-22")
def test_p22a_every_implemented_api_route_is_in_the_contract():
    assert not _undocumented() & KNOWN_UNDOCUMENTED


@defects.xfail("DEF-P22-004")
@pytest.mark.req("UR-22")
def test_p22b_wave2_module_routes_are_in_the_contract():
    assert not _undocumented() & KNOWN_UNDOCUMENTED_W02


def test_p22b_route_templates_expand_literal_regex_alternations():
    """``(?P<slug>a|b)`` routes (ledger dimensions) expand to one template per literal before matching."""
    from qa_harness.routes import _expand_groups

    assert _expand_groups("x/(?P<slug>cost-centers|business-units)/(?P<node_id>[0-9a-f-]{36})") == [
        "x/cost-centers/{node_id}", "x/business-units/{node_id}",
    ]  # fmt: skip


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
