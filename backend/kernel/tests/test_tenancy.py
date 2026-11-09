import uuid

import pytest
from django.db import connection, transaction

from kernel import db, ids, tenancy
from kernel.errors import TenancyNotBound, ValidationFailed
from kernel.tenancy import Channel, PrincipalType, TenantContext
from kernel.testing.tenancy import first_company, make_context

TABLE = "kernel_test_rls_probe"


@pytest.fixture
def rls_table(django_db_blocker):
    forward, reverse = db.rls_policy_sql(TABLE, company_scoped=True)
    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute(f"CREATE TABLE {TABLE} (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, company_id uuid NOT NULL)")
        cur.execute(forward)
    yield
    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute(reverse)
        cur.execute(f"DROP TABLE {TABLE}")


def _count() -> int:
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {TABLE}")
        return cur.fetchone()[0]


def _insert(ctx: TenantContext, company: uuid.UUID) -> None:
    with transaction.atomic():
        tenancy.bind(ctx)
        with connection.cursor() as cur:
            cur.execute(f"INSERT INTO {TABLE} VALUES (%s, %s, %s)", [ids.new_id(), ctx.tenant_id, company])


@pytest.mark.django_db(transaction=True)
def test_T_P01_10_binding_cleared_after_transaction_on_pooled_connection(rls_table):
    a, b = make_context(), make_context()
    _insert(a, first_company(a))
    _insert(b, first_company(b))
    connection.ensure_connection()
    backend_pid = connection.connection.info.backend_pid

    with transaction.atomic():
        tenancy.bind(a)
        assert tenancy.bound_settings()["app.tenant_id"] == str(a.tenant_id)
        assert _count() == 1
    # same physical connection, next transaction: binding is gone → zero rows
    assert connection.connection.info.backend_pid == backend_pid
    assert tenancy.bound_settings()["app.tenant_id"] == ""
    with transaction.atomic():
        assert _count() == 0
    with transaction.atomic():
        tenancy.bind(b)
        assert _count() == 1
    # rollback also clears
    with pytest.raises(RuntimeError), transaction.atomic():
        tenancy.bind(a)
        raise RuntimeError("boom")
    assert tenancy.bound_settings()["app.tenant_id"] == ""
    assert _count() == 0


@pytest.mark.django_db(transaction=True)
def test_T_P01_10_company_scope_and_write_check(rls_table):
    ctx = make_context(company_ids=[ids.new_id(), ids.new_id()])
    c1, c2 = sorted(ctx.company_ids)
    _insert(ctx, c1)
    _insert(ctx, c2)
    only_c1 = TenantContext(ctx.tenant_id, frozenset([c1]), ctx.principal_id)
    with tenancy.scope(only_c1):
        assert _count() == 1
    with pytest.raises(Exception), transaction.atomic():  # RLS WITH CHECK refuses foreign company
        tenancy.bind(only_c1)
        with connection.cursor() as cur:
            cur.execute(f"INSERT INTO {TABLE} VALUES (%s, %s, %s)", [ids.new_id(), ctx.tenant_id, c2])


@pytest.mark.django_db(transaction=True)
def test_T_P01_10_bind_requires_transaction():
    ctx = make_context()
    with pytest.raises(TenancyNotBound):
        tenancy.bind(ctx)


def test_T_P01_10_context_validation_and_serialization():
    ctx = make_context(principal_type=PrincipalType.USER, channel=Channel.UI)
    assert TenantContext.from_dict(ctx.to_dict()) == ctx
    values = ctx.settings_values()
    assert values["app.company_ids"].startswith("{") and values["app.channel"] == "ui"
    with pytest.raises(ValidationFailed):
        TenantContext("nope", frozenset(), ids.new_id())  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        make_context(principal_type=PrincipalType.AI_AGENT)  # needs on_behalf_of
    agent = make_context(principal_type=PrincipalType.MCP_CLIENT, on_behalf_of=ids.new_id(), channel=Channel.MCP)
    assert agent.settings_values()["app.on_behalf_of"] == str(agent.on_behalf_of)
    assert TenantContext.from_dict(agent.to_dict()) == agent
    with pytest.raises(ValidationFailed):
        ctx.require_company(ids.new_id())
    assert ctx.can_access_company(first_company(ctx))
    with pytest.raises(ValueError):
        TenantContext(ids.new_id(), frozenset(), ids.new_id(), channel="telnet")  # type: ignore[arg-type]


def test_T_P01_10_activate_and_require_current():
    assert tenancy.current() is None
    with pytest.raises(TenancyNotBound):
        tenancy.require_current()
    ctx = make_context()
    with tenancy.activate(ctx):
        assert tenancy.require_current() is ctx
    assert tenancy.current() is None


def test_T_P01_10_middleware_activates_request_context(settings, rf):
    ctx = make_context()
    seen = []
    mw = tenancy.TenancyMiddleware(lambda req: seen.append(tenancy.current()) or "resp")
    request = rf.get("/")
    request.erp_context = ctx
    assert mw(request) == "resp"
    plain = rf.get("/")
    mw(plain)
    settings.ERP_TENANT_CONTEXT_RESOLVER = "kernel.tests.test_tenancy.resolver_for_test"
    mw(rf.get("/"))
    assert seen[0] is ctx and seen[1] is None and seen[2] == RESOLVED
    assert tenancy.current() is None


RESOLVED = make_context()


def resolver_for_test(request):
    return RESOLVED
