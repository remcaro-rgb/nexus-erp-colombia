"""P22a harness self-tests: principal matrix and permission evaluation from the frozen role templates."""

from __future__ import annotations

from qa_harness.principals import MCP_SCOPES, Kind, matrix, roles, synthetic_id


def test_p22a_matrix_is_two_tenants_three_companies_seven_kinds():
    m = matrix()
    assert len(m.tenants) == 2 and m.tenants[0].id != m.tenants[1].id
    for t in m.tenants:
        assert len(set(t.company_ids)) == 3
    assert len(m.principals) == 14
    assert {p.kind for p in m.principals} == set(Kind)
    assert len({p.id for p in m.principals}) == 14
    assert not set(m.tenants[0].company_ids) & set(m.tenants[1].company_ids)
    for p in m.principals:
        assert p.company_ids <= set(p.tenant.company_ids)


def test_p22a_ids_are_deterministic_and_synthetic():
    assert synthetic_id("T1") == matrix().tenants[0].id
    assert synthetic_id("T1", "company1") == matrix().tenants[0].company_ids[0]


def test_p22a_role_templates_deny_wins():
    m = matrix()
    admin = m.of("T1", Kind.ADMIN)
    assert admin.allows("iam.user.create")
    assert admin.allows("sales.invoice.read")
    assert not admin.allows("sales.invoice.post"), "administrador never posts (deny *.*.post)"
    auditor = m.of("T1", Kind.AUDITOR)
    assert auditor.allows("ledger.entry.read")
    for code in ("ledger.entry.create", "ledger.entry.post", "sales.invoice.export", "payroll.run.approve"):
        assert not auditor.allows(code), code
    assert not auditor.allows(""), "deny by default for operations without a permission"


def test_p22a_ai_delegated_is_bounded_by_user_and_ceiling():
    m = matrix()
    ai, user = m.of("T1", Kind.AI_DELEGATED), m.of("T1", Kind.SALESPERSON)
    assert ai.on_behalf_of == user
    assert ai.allows("sales.quote.create") and user.allows("sales.quote.create")
    assert not ai.allows("sales.quote.approve")
    assert not ai.allows("iam.user.read"), "never beyond the delegating user"
    codes = [c["code"] for c in __import__("qa_harness.paths", fromlist=["x"]).permission_catalog()["codes"]]
    assert all(user.allows(c) for c in codes if ai.allows(c))
    assert not any(ai.allows(c) for c in codes if c.endswith((".post", ".approve", ".void", ".delete", ".admin")))
    assert ai.max_classification == "confidential"


def test_p22a_mcp_client_is_bounded_by_scopes():
    m = matrix()
    mcp = m.of("T2", Kind.MCP_LIMITED)
    assert mcp.scopes == MCP_SCOPES
    assert mcp.allows("inventory.item.read") and mcp.allows("sales.party.read")
    assert not mcp.allows("ledger.entry.read"), "admin may read it, the MCP scope does not"
    assert not mcp.allows("inventory.item.create")
    assert mcp.max_classification == "internal"


def test_p22a_principals_build_valid_kernel_tenant_contexts():
    from kernel.tenancy import TenantContext

    for p in matrix().principals:
        ctx = TenantContext(**p.tenant_context_kwargs())
        assert ctx.tenant_id == p.tenant.id
        assert ctx.principal_type.value == p.principal_type
        if p.kind in (Kind.AI_DELEGATED, Kind.MCP_LIMITED):
            assert ctx.on_behalf_of is not None


def test_p22a_every_role_template_is_loaded():
    assert {"administrador", "vendedor", "jefe_bodega", "analista_nomina", "auditor", "agente_ia"} <= set(roles())
